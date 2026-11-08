"""增量索引单测（设计 §5）：防抖、队列合并/背压、真实 watchdog 增量 E2E。

E2E 在临时目录跑真实 watchdog + 单线程 worker（debounce=0.3s、CPU 阈值 200 禁用空闲等待），
覆盖：启动全量扫描、新建/修改/删除文件、同内容重写不重建、目录新建/删除。
运行：python -m unittest tests.test_watcher
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.directory import DirectoryConnector
from connectors.excludes import ExclusionEngine
from indexer.db import IndexDB
from indexer.indexer import Indexer
from indexer.jieba_util import build_phrase_match, query_tokens
from indexer.watcher import Debouncer, JobQueue, WatchService


def phrase(q: str) -> str:
    """复用生产函数 build_phrase_match（reviewer 建议②：测试与生产同源）。"""
    return build_phrase_match(query_tokens(q))


class DebouncerTest(unittest.TestCase):
    def test_coalesce_same_key(self) -> None:
        fired: list[str] = []
        d = Debouncer(delay=0.3)
        d.schedule("a", lambda: fired.append("a1"))
        time.sleep(0.1)
        d.schedule("a", lambda: fired.append("a2"))  # 重置计时器
        time.sleep(0.6)
        self.assertEqual(fired, ["a2"])  # 只触发最新一次
        d.shutdown()

    def test_distinct_keys(self) -> None:
        fired: list[str] = []
        d = Debouncer(delay=0.2)
        d.schedule("a", lambda: fired.append("a"))
        d.schedule("b", lambda: fired.append("b"))
        time.sleep(0.5)
        self.assertEqual(sorted(fired), ["a", "b"])
        d.shutdown()


class JobQueueTest(unittest.TestCase):
    def test_merge_same_key(self) -> None:
        q = JobQueue(maxsize=10)
        self.assertTrue(q.put("k", lambda: None))
        self.assertTrue(q.put("k", lambda: None))  # 合并
        self.assertEqual(q.qsize(), 1)
        self.assertIsNotNone(q.get(timeout=0.1))
        self.assertIsNone(q.get(timeout=0.1))  # 空 -> None

    def test_backpressure_drop_new(self) -> None:
        q = JobQueue(maxsize=1)
        self.assertTrue(q.put("k1", lambda: None))
        self.assertFalse(q.put("k2", lambda: None))  # 满 -> 丢新（设计 §12）
        self.assertEqual(q.qsize(), 1)


class WatchE2ETest(unittest.TestCase):
    """真实 watchdog + worker 的增量链路。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "notes"
        self.root.mkdir()
        (self.root / "a.md").write_text("# A\n初始词条甲。\n", encoding="utf-8")
        (self.root / "b.md").write_text("# B\n初始词条乙。\n", encoding="utf-8")
        self.db = IndexDB(base / "w.db")
        self.engine = ExclusionEngine()
        self.connector = DirectoryConnector(self.root, engine=self.engine)
        self.indexer = Indexer(self.db)
        self.mount_id = self.db.add_mount("directory", str(self.root))
        self.service = WatchService(
            self.indexer, self.db, debounce=0.3, cpu_threshold=200.0, worker_yield=0.0
        )
        self.service.add_mount(self.mount_id, self.connector)
        self.service.enqueue_scan(self.mount_id, self.connector)
        self.worker = self.service.start_worker()

    def tearDown(self) -> None:
        self.service.stop_worker()
        self.service.shutdown()
        self.db.close()
        self.tmp.cleanup()

    # ---- 工具 ----
    def _fts(self, q: str) -> int:
        rows = self.db.query(
            "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?", (q,)
        )
        return int(rows[0]["n"])

    def _wait(self, cond, timeout: float = 6.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.1)
        self.fail("条件在 {} 秒内未满足".format(timeout))

    def _wait_indexed(self, n: int) -> None:
        self._wait(lambda: self.db.stats()["indexed"] >= n)

    def _wait_match(self, q: str, n: int) -> None:
        self._wait(lambda: self._fts("content_jieba : " + phrase(q)) >= n)

    # ---- 用例 ----
    def test_initial_scan(self) -> None:
        self._wait_indexed(2)
        self.assertGreaterEqual(self._fts("content_jieba : " + phrase("初始词条甲")), 1)
        self.assertGreaterEqual(self._fts("content_jieba : " + phrase("初始词条乙")), 1)

    def test_create_file_indexed(self) -> None:
        self._wait_indexed(2)
        (self.root / "c.md").write_text("# C\n新文件词条丙。\n", encoding="utf-8")
        self._wait_match("新文件词条丙", 1)

    def test_modify_file_reindexed(self) -> None:
        self._wait_indexed(2)
        with open(self.root / "a.md", "a", encoding="utf-8") as f:
            f.write("\n追加词条丁。\n")
        self._wait_match("追加词条丁", 1)

    def test_delete_file_removed(self) -> None:
        self._wait_indexed(2)
        (self.root / "b.md").unlink()
        self._wait(lambda: self.db.get_doc(self.mount_id, "b.md") is None)

    def test_same_content_rewrite_no_reindex(self) -> None:
        # 同内容重写（mtime 变化、内容不变）：哈希兜底 -> 不重建 chunk
        self._wait_indexed(2)
        doc = self.db.get_doc(self.mount_id, "a.md")
        updated_at = doc.updated_at
        path = self.root / "a.md"
        path.write_bytes(path.read_bytes())
        time.sleep(1.5)  # 等待事件与防抖窗口过去
        doc2 = self.db.get_doc(self.mount_id, "a.md")
        self.assertEqual(doc2.updated_at, updated_at)  # 未重建索引

    def test_dir_created_scanned(self) -> None:
        self._wait_indexed(2)
        sub = self.root / "新建目录"
        sub.mkdir()
        (sub / "d.md").write_text("# D\n目录新文件词条戊。\n", encoding="utf-8")
        self._wait_match("目录新文件词条戊", 1)

    def test_dir_deleted_removed(self) -> None:
        self._wait_indexed(2)
        sub = self.root / "已有目录"
        sub.mkdir()
        (sub / "e.md").write_text("# E\n将删除词条己。\n", encoding="utf-8")
        self._wait_match("将删除词条己", 1)
        shutil.rmtree(sub)
        self._wait(lambda: self.db.get_doc(self.mount_id, "已有目录/e.md") is None)


if __name__ == "__main__":
    unittest.main()
