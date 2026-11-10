"""共享状态 / 挂载暂停的检索过滤。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB
from indexer.jieba_util import tokenize
from searcher.search import Searcher


class SharingPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = IndexDB(Path(self.tmp.name) / "s.db")
        self.mount = self.db.add_mount("directory", str(Path(self.tmp.name) / "notes"))
        self.doc = self.db.upsert_doc(
            self.mount, "a.md", str(Path(self.tmp.name) / "notes" / "a.md"),
            "文件锁", "md", 1, 10, "h", "indexed",
        )
        self.db.set_chunks(self.doc, ["文件锁的共享锁和独占锁用法"], tokenize)
        self.local = Searcher(self.db)
        self.remote = Searcher(self.db, scope="remote")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_private_local_yes_remote_no_hidden_neither(self):
        self.assertTrue(self.local.search("文件锁"))
        self.assertTrue(self.remote.search("文件锁"))
        self.db.set_doc_share(self.doc, "private")
        self.assertTrue(self.local.search("文件锁"))
        self.assertEqual(self.remote.search("文件锁"), [])
        self.db.set_doc_share(self.doc, "hidden")
        self.assertEqual(self.local.search("文件锁"), [])
        self.assertEqual(self.remote.search("文件锁"), [])

    def test_paused_mount_excluded(self):
        self.assertTrue(self.local.search("文件锁"))
        self.db.set_mount_enabled(self.mount, False)
        self.assertEqual(self.local.search("文件锁"), [])
        self.assertEqual(self.remote.search("文件锁"), [])
        self.db.set_mount_enabled(self.mount, True)
        self.assertTrue(self.local.search("文件锁"))

    def test_doc_share_state_roundtrip(self):
        self.db.set_doc_share(self.doc, "private")
        self.assertEqual(self.db.get_doc_by_id(self.doc).share_state, "private")


if __name__ == "__main__":
    unittest.main()
