"""fetch 前复核文档 hash：搜索后文档被修改要标记 version_changed。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB
from indexer.jieba_util import tokenize
from retrieval.kernel import RetrievalKernel
from retrieval.session import EvidenceSession


class HashVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = IndexDB(Path(self.tmp.name) / "s.db")
        self.mount = self.db.add_mount("directory", str(Path(self.tmp.name) / "notes"))
        self.doc = self.db.upsert_doc(
            self.mount, "a.md", str(Path(self.tmp.name) / "notes" / "a.md"),
            "a", "md", 1, 10, "hash-1", "indexed",
        )
        self.db.set_chunks(self.doc, ["第一版正文"], tokenize)
        self.agent = SimpleNamespace(
            db=self.db,
            config=SimpleNamespace(relay_nick="我"),
            searcher=None,
            bus=None,
        )
        self.kernel = RetrievalKernel(self.agent)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _session(self):
        s = EvidenceSession("q")
        s.add_hits([{
            "doc_id": self.doc, "title": "a", "rel_path": "a.md", "format": "md",
            "tier": "strong", "score": 1.0, "snippet_text": "第一版正文",
            "sources": [{"node": "我", "doc_id": self.doc}],
        }])
        asyncio.run(self.kernel.hydrate(s))
        return s

    def test_version_changed(self):
        s = self._session()
        ev = asyncio.run(self.kernel.fetch(s, "e1", max_chars=1000))
        self.assertFalse(ev.version_changed)
        self.db.upsert_doc(
            self.mount, "a.md", str(Path(self.tmp.name) / "notes" / "a.md"),
            "a", "md", 2, 10, "hash-2", "indexed",
        )
        self.db.set_chunks(self.doc, ["第二版正文"], tokenize)
        ev = asyncio.run(self.kernel.fetch(s, "e1", max_chars=1000, mode="full"))
        self.assertTrue(ev.version_changed)
        self.assertIn("第二版", ev.full_text)

    def test_deleted_rejected(self):
        s = self._session()
        self.db.delete_doc(self.mount, "a.md")
        with self.assertRaises(LookupError):
            asyncio.run(self.kernel.fetch(s, "e1", max_chars=1000))


if __name__ == "__main__":
    unittest.main()
