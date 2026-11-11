"""文件名/目录元数据检索：香港.md 这类信息源。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB
from indexer.jieba_util import tokenize
from searcher.search import Searcher


class MetaSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = IndexDB(Path(self.tmp.name) / "s.db")
        self.mount = self.db.add_mount("directory", str(Path(self.tmp.name) / "notes"))
        self.hk = self._doc("香港.md", "香港", "IP 段：10.0.0.0/8，内部出口地址")
        self.bj = self._doc("data/北京.md", "北京", "IP 段：192.168.0.0/16")
        self.other = self._doc("杂项.md", "杂项", "没有网络资料")
        self.s = Searcher(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _doc(self, rel: str, title: str, text: str) -> int:
        doc_id = self.db.upsert_doc(
            self.mount, rel, str(Path(self.tmp.name) / "notes" / rel), title, "md", 1, 10, rel, "indexed"
        )
        self.db.set_chunks(doc_id, [text], tokenize)
        return doc_id

    def test_filename_is_information_source(self):
        hits = self.s.search("香港 IP", 8)
        self.assertTrue(hits)
        self.assertEqual(hits[0].doc_id, self.hk)
        self.assertIn("title", hits[0].match_fields)
        self.assertIn("content", hits[0].match_fields)

    def test_directory_matches_but_lower_than_filename(self):
        hits = self.s.search("北京 IP", 8)
        self.assertTrue(hits)
        self.assertEqual(hits[0].doc_id, self.bj)
        self.assertIn("path", hits[0].match_fields)
        self.assertIn("content", hits[0].match_fields)

    def test_filename_only_hit_is_returned_but_weak(self):
        hits = self.s.search("香港", 8)
        self.assertTrue(hits)
        self.assertEqual(hits[0].doc_id, self.hk)
        self.assertEqual(hits[0].tier, "weak")

    def test_meta_updates_on_rename_and_delete(self):
        hits = self.s.search("香港", 8)
        self.assertEqual(hits[0].doc_id, self.hk)
        self.db.upsert_doc(
            self.mount, "香港.md", str(Path(self.tmp.name) / "notes" / "香港.md"),
            "香港", "md", 2, 11, "h2", "indexed",
        )
        self.db.delete_doc(self.mount, "香港.md")
        self.assertEqual(self.s.search("香港", 8), [])

    def test_private_not_visible_to_remote(self):
        self.db.set_doc_share(self.hk, "private")
        remote = Searcher(self.db, scope="remote")
        self.assertNotIn(self.hk, [h.doc_id for h in remote.search("香港 IP", 8)])
        self.db.set_doc_share(self.hk, "shared")
        self.assertIn(self.hk, [h.doc_id for h in remote.search("香港 IP", 8)])


if __name__ == "__main__":
    unittest.main()
