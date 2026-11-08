"""P0.5 检索质量回归：复合词短语、泛词不泛滥、弱相关分层。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB
from indexer.jieba_util import tokenize
from searcher.search import Searcher


DOCS = [
    ("文件锁", "文件锁是系统调用 fcntl 的加锁示例；支持共享锁与独占锁，按字节范围锁定。"),
    ("文件外发审批", "文件外发审批流程：提交申请、部门审批、邮件通知。"),
    ("文件导入导出", "文件导入导出操作说明：支持批量导入与模板导出。"),
    ("git lfs", "git lfs 大文件存储使用说明与常见问题。"),
    ("013_查看进程启动参数的方法", "进程启动参数查看方法：/proc/<pid>/cmdline 与 ps 命令。"),
    ("数据库索引", "数据库索引优化：B+ 树、覆盖索引与回表。"),
]


class RetrievalQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cw-ret-"))
        self.db = IndexDB(self.tmp / "r.db")
        mount = self.db.add_mount("directory", str(self.tmp))
        for title, text in DOCS:
            doc_id = self.db.upsert_doc(
                mount, title + ".md", str(self.tmp / (title + ".md")), title, "md", 1, 10, title, "indexed"
            )
            self.db.set_chunks(doc_id, [text], tokenize)
        self.searcher = Searcher(self.db)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _titles(self, q: str, topk: int = 8):
        hits = self.searcher.search(q, topk)
        return [h.title for h in hits if h.tier != "weak"]

    def test_compound_phrase_not_diluted(self) -> None:
        titles = self._titles("文件锁怎么用")
        self.assertEqual(titles[0], "文件锁")
        self.assertNotIn("文件外发审批", titles)
        self.assertNotIn("文件导入导出", titles)

    def test_exact_term(self) -> None:
        self.assertEqual(self._titles("文件外发审批")[0], "文件外发审批")

    def test_multi_term(self) -> None:
        self.assertEqual(self._titles("进程启动参数")[0], "013_查看进程启动参数的方法")

    def test_generic_word_alone_not_flood(self) -> None:
        titles = self._titles("文件锁", topk=5)
        self.assertEqual(titles[0], "文件锁")
        self.assertLessEqual(len(titles), 3)


if __name__ == "__main__":
    unittest.main()
