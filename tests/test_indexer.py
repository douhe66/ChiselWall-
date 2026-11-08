"""索引管线单测（设计 §5）：全量扫描、FTS5+jieba 命中、增量校验、失败隔离、排除红线。

先运行 tests/make_fixtures.py 生成测试数据（fixtures 会被复制到临时目录再索引，
测试只写临时副本，绝不触碰 fixtures 原件）。
运行：python -m unittest tests.test_indexer
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.directory import DirectoryConnector
from connectors.excludes import ExcludeRule, ExclusionEngine
from indexer.db import IndexDB
from indexer.indexer import Indexer, file_hash
from indexer.jieba_util import build_phrase_match, query_tokens

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notes"


def phrase(q: str) -> str:
    """与入库同源分词（lcut_for_search）构造 FTS5 短语查询。

    直接复用生产函数 build_phrase_match（reviewer 建议②：测试与生产同源，
    避免两处实现漂移）。
    """
    return build_phrase_match(query_tokens(q))


class IndexerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db_path = base / "chiselwall.db"  # 红线②：DB 独立于挂载目录之外
        self.mount_root = base / "notes"
        shutil.copytree(FIXTURES, self.mount_root)
        self.db = IndexDB(self.db_path)
        self.engine = ExclusionEngine()
        self.connector = DirectoryConnector(self.mount_root, engine=self.engine)
        self.indexer = Indexer(self.db)
        self.mount_id = self.db.add_mount("directory", str(self.mount_root))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _fts(self, q: str) -> int:
        rows = self.db.query(
            "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?", (q,)
        )
        return int(rows[0]["n"])

    def _scan(self) -> dict:
        return self.indexer.scan_mount(self.mount_id, self.connector)

    # ---- 全量扫描与状态 ----
    def test_full_scan_statuses(self) -> None:
        stats = self._scan()
        s = self.db.stats()
        self.assertEqual(s["pending"], 0)
        self.assertGreaterEqual(s["indexed"], 15)   # 16 个可索引文件（含单独元宝.json、孪生.md）
        self.assertGreaterEqual(s["excluded"], 9)   # 隐藏/~$/tmp/二进制/旧格式/零字节/不支持格式x2/孪生.json
        self.assertEqual(s["error"], 1)             # corrupt.docx
        self.assertEqual(stats["removed"], 0)       # 新库扫描无删除
        # M2-FIX4：排除构成分类（默认 6 / 不支持格式 2 / 孪生去重 1 / 用户规则 0）
        self.assertEqual(
            s["excluded_by_reason"],
            {"user_rule": 0, "default": 6, "unsupported_format": 2, "duplicate_md_twin": 1},
        )

    def test_scan_idempotent(self) -> None:
        self._scan()
        stats = self._scan()
        self.assertEqual(stats["indexed"], 0)
        self.assertGreaterEqual(stats["unchanged"], 16)

    def test_scan_removes_gone_docs(self) -> None:
        self._scan()
        (self.mount_root / "note.txt").unlink()
        stats = self._scan()
        self.assertGreaterEqual(stats["removed"], 1)
        self.assertIsNone(self.db.get_doc(self.mount_id, "note.txt"))

    # ---- FTS5 + jieba 命中 ----
    def test_fts_chinese_hit(self) -> None:
        self._scan()
        self.assertGreaterEqual(self._fts("content_jieba : " + phrase("凿壁测试词条")), 1)
        self.assertGreaterEqual(self._fts("content_jieba : " + phrase("部门知识共享")), 1)

    def test_fts_formats_indexed(self) -> None:
        self._scan()
        for term in ("gbk词条测试", "docx词条测试", "xlsx词条测试", "pdfwordterm", "子目录词条"):
            self.assertGreaterEqual(self._fts("content_jieba : " + phrase(term)), 1, term)

    def test_trigram_table_filled_when_enabled(self) -> None:
        # F2 修复后 trigram 默认关闭：显式开启才写兜底表。
        # FTS5 trigram 语义：≥3 字的 token 按 3 字滑窗建 gram，可命中任意 ≥3 字子串；
        # <3 字的 token 整词存储（2 字词由 jieba 影子列覆盖，见 searcher 路由注释）。
        idx = Indexer(self.db, trigram_enabled=True)
        idx.scan_mount(self.mount_id, self.connector)
        rows = self.db.query(
            "SELECT COUNT(*) AS n FROM chunks_trigram WHERE chunks_trigram MATCH ?", ('"凿壁测"',)
        )
        self.assertGreaterEqual(int(rows[0]["n"]), 1)

    def test_trigram_disabled_by_default(self) -> None:
        # F2：默认不写 trigram 兜底表（空间保护）
        self._scan()
        rows = self.db.query("SELECT COUNT(*) AS n FROM chunks_trigram")
        self.assertEqual(int(rows[0]["n"]), 0)

    def test_trigram_small_doc_only(self) -> None:
        # F2：开启时仅总字符数 ≤ 阈值的文档写 trigram
        idx = Indexer(self.db, trigram_enabled=True, trigram_max_chars=5000)
        idx.scan_mount(self.mount_id, self.connector)
        small = self.db.query(
            "SELECT COUNT(*) AS n FROM chunks_trigram f JOIN chunks c ON c.chunk_id=f.rowid "
            "JOIN docs d ON d.doc_id=c.doc_id WHERE d.rel_path=?",
            ("中文笔记.md",),
        )[0]["n"]
        self.assertGreaterEqual(int(small), 1)
        big = self.db.query(
            "SELECT COUNT(*) AS n FROM chunks_trigram f JOIN chunks c ON c.chunk_id=f.rowid "
            "JOIN docs d ON d.doc_id=c.doc_id WHERE d.rel_path=?",
            ("大文件.txt",),
        )[0]["n"]
        self.assertEqual(int(big), 0)  # 6000 字 > 阈值，不写 trigram

    def test_big_reindex_does_not_block_search(self) -> None:
        # F1：2000 chunk 大文档重索引分批提交、批间释放锁，检索可穿插（<500ms）
        self._scan()
        doc = self.db.get_doc(self.mount_id, "中文笔记.md")
        chunks = ["搜索目标内容" * 60] * 2000  # 每块 360 字 × 2000
        started = threading.Event()
        done = threading.Event()
        def writer() -> None:
            started.set()
            self.db.set_chunks(doc.doc_id, chunks, lambda t: t)
            done.set()
        t = threading.Thread(target=writer)
        t.start()
        self.assertTrue(started.wait(2))
        worst = 0.0
        while not done.is_set():
            t0 = time.perf_counter()
            n = self._fts("content_jieba : " + phrase("gbk词条测试"))
            worst = max(worst, time.perf_counter() - t0)
            self.assertGreaterEqual(n, 1)  # 其他文档始终可检索
            time.sleep(0.001)
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertLess(worst, 0.5, "大文档重索引期间检索最差延迟 %.3fs ≥ 0.5s" % worst)
        self.assertEqual(len(self.db.get_chunk_texts(doc.doc_id)), 2000)

    # ---- 增量校验（mtime/size/hash） ----
    def test_unchanged_fast_path(self) -> None:
        self._scan()
        res = self.indexer.index_file(self.mount_id, self.connector, "中文笔记.md")
        self.assertEqual(res, "unchanged")

    def test_same_content_new_mtime(self) -> None:
        # 内容不变仅 mtime 变化（同内容重写）：哈希兜底判定 unchanged
        self._scan()
        path = self.mount_root / "中文笔记.md"
        path.write_bytes(path.read_bytes())
        res = self.indexer.index_file(self.mount_id, self.connector, "中文笔记.md")
        self.assertEqual(res, "unchanged")

    def test_update_reindex(self) -> None:
        self._scan()
        with open(self.mount_root / "中文笔记.md", "a", encoding="utf-8") as f:
            f.write("\n\n新增词条甲乙丙丁在此。\n")
        res = self.indexer.index_file(self.mount_id, self.connector, "中文笔记.md")
        self.assertEqual(res, "indexed")
        self.assertGreaterEqual(self._fts("content_jieba : " + phrase("新增词条甲乙丙丁")), 1)

    def test_content_change_removes_old_terms(self) -> None:
        self._scan()
        (self.mount_root / "中文笔记.md").write_text("# 重写\n内容已完全不同。\n", encoding="utf-8")
        self.indexer.index_file(self.mount_id, self.connector, "中文笔记.md")
        self.assertEqual(self._fts("content_jieba : " + phrase("凿壁测试词条")), 0)

    def test_delete_file(self) -> None:
        self._scan()
        (self.mount_root / "note.txt").unlink()
        res = self.indexer.index_file(self.mount_id, self.connector, "note.txt")
        self.assertEqual(res, "removed")
        self.assertIsNone(self.db.get_doc(self.mount_id, "note.txt"))
        self.assertEqual(self._fts("content_jieba : " + phrase("utf8词条测试")), 0)

    # ---- 排除红线（红线⑤） ----
    def test_exclude_redline(self) -> None:
        self._scan()
        doc = self.db.get_doc(self.mount_id, "排除我/secret.md")
        self.assertEqual(doc.status, "indexed")
        self.assertGreater(len(self.db.get_chunk_texts(doc.doc_id)), 0)
        # 添加目录排除规则 → 重扫：状态 excluded、chunk 摘除、检索不可得
        pattern = str(self.mount_root / "排除我")
        self.db.add_exclude_rule("dir", pattern)
        self.engine.set_rules([ExcludeRule("dir", pattern)])
        self._scan()
        doc2 = self.db.get_doc(self.mount_id, "排除我/secret.md")
        self.assertEqual(doc2.status, "excluded")
        self.assertEqual(len(self.db.get_chunk_texts(doc2.doc_id)), 0)
        self.assertEqual(self._fts("content_jieba : " + phrase("机密词条")), 0)

    def test_exclude_removal_reindexes(self) -> None:
        pattern = str(self.mount_root / "排除我")
        self.db.add_exclude_rule("dir", pattern)
        self.engine.set_rules([ExcludeRule("dir", pattern)])
        self._scan()
        # 新库首次扫描：被排除目录整棵剪枝，文档根本不建档（隐私：完全不可见）
        self.assertIsNone(self.db.get_doc(self.mount_id, "排除我/secret.md"))
        # 移除规则 → 重扫：恢复索引
        rules = self.db.list_exclude_rules()
        for rule_id, _kind, _p in rules:
            self.db.remove_exclude_rule(rule_id)
        self.engine.set_rules([])
        self._scan()
        doc = self.db.get_doc(self.mount_id, "排除我/secret.md")
        self.assertEqual(doc.status, "indexed")
        self.assertGreaterEqual(self._fts("content_jieba : " + phrase("机密词条")), 1)

    # ---- 失败隔离（红线④） ----
    def test_parse_error_isolated(self) -> None:
        self._scan()
        s = self.db.stats()
        self.assertEqual(s["error"], 1)  # 仅 corrupt.docx 失败
        self.assertGreaterEqual(s["indexed"], 13)  # 不阻断其他文件
        failed = self.db.failed_docs()
        self.assertTrue(any("corrupt.docx" in f["rel_path"] for f in failed))

    def test_file_hash(self) -> None:
        data = b"hello chiselwall"
        p = Path(self.tmp.name) / "h.bin"
        p.write_bytes(data)
        self.assertEqual(file_hash(p, len(data)), hashlib.sha256(data).hexdigest())

    def test_old_schema_migration(self) -> None:
        """M2-FIX2 回归：旧 schema 库（chunks 带 text_jieba NOT NULL）打开即迁移，扫描可写入。

        生产事故复盘：旧库未迁移时 set_chunks 不写 text_jieba → IntegrityError
        （NOT NULL constraint failed: chunks.text_jieba）→ 扫描反复失败、indexed=0。
        """
        import sqlite3 as _sq

        from indexer.db import SCHEMA

        # 构造旧 schema：现 SCHEMA 的 chunks 表加回 text_jieba NOT NULL 列
        old_schema = SCHEMA.replace(
            "  text TEXT NOT NULL\n);", "  text TEXT NOT NULL,\n  text_jieba TEXT NOT NULL\n);"
        )
        path = Path(self.tmp.name) / "old.db"
        raw = _sq.connect(str(path))
        raw.executescript(old_schema)
        raw.commit()
        raw.close()
        # 打开旧库：IndexDB.__init__ 自动迁移删列
        db = IndexDB(path)
        cols = {r["name"] for r in db.query("PRAGMA table_info(chunks)")}
        self.assertNotIn("text_jieba", cols)
        # 迁移后正常挂载扫描：不再有 NOT NULL 报错
        conn = DirectoryConnector(self.mount_root, engine=self.engine)
        idx = Indexer(db)
        mid = db.add_mount("directory", str(self.mount_root))
        idx.scan_mount(mid, conn)
        self.assertGreaterEqual(db.stats()["indexed"], 13)
        self.assertEqual(db.stats()["pending"], 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
