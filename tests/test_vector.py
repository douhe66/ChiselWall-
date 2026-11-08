"""M7 向量检索单测：sqlite-vec 存取、混合检索融合（FakeEmbeddings）。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.embedding import FakeEmbeddings
from ai.vector_index import VectorIndex
from ai.vectorstore import VectorStore
from indexer.db import IndexDB
from searcher.search import Searcher


class VectorBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cw-vec-"))
        self.db = IndexDB(self.tmp / "v.db")
        self.store = None
        self.mount = self.db.add_mount("directory", str(self.tmp))

    def tearDown(self) -> None:
        if self.store is not None:
            self.store.close()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_doc(self, rel: str, title: str, text: str) -> int:
        doc_id = self.db.upsert_doc(
            self.mount, rel, str(self.tmp / rel), title, "md", 1.0, 10, rel, "indexed"
        )
        self.db.set_chunks([text], lambda s: s) if False else self.db.set_chunks([], lambda s: s)
        return doc_id


def make_db(tmp: Path):
    db = IndexDB(tmp / "v.db")
    mount = db.add_mount("directory", str(tmp))
    doc1 = db.upsert_doc(mount, "a.md", str(tmp / "a.md"), "文件锁", "md", 1.0, 10, "h1", "indexed")
    db.set_chunks(doc1, ["fcntl 是系统调用，用于文件描述符控制。"], lambda s: s)
    doc2 = db.upsert_doc(mount, "b.md", str(tmp / "b.md"), "Vue 组件", "md", 1.0, 10, "h2", "indexed")
    db.set_chunks(doc2, ["Vue 组件通信 props emit 用法。"], lambda s: s)
    return db, mount, doc1, doc2


class VectorStoreTest(unittest.TestCase):
    def _setup(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cw-vec-"))
        db, mount, doc1, doc2 = make_db(self.tmpdir)
        store = VectorStore(db.path, dim=32)
        vi = VectorIndex(db, store, FakeEmbeddings(dim=32))
        return db, store, vi, doc1, doc2

    def tearDown(self) -> None:
        if getattr(self, "store", None) is not None:
            self.store.close()
        if getattr(self, "db", None) is not None:
            self.db.close()
        shutil.rmtree(getattr(self, "tmpdir", Path(".")), ignore_errors=True)

    def test_upsert_and_search(self) -> None:
        self.db, self.store, vi, doc1, _doc2 = self._setup()
        n = vi.index_doc(doc1)
        self.assertEqual(n, 1)
        self.assertEqual(self.store.count(), 1)
        ids = vi.search_ids("fcntl 文件锁", k=5)
        self.assertEqual(len(ids), 1)

    def test_index_all(self) -> None:
        self.db, self.store, vi, _doc1, _doc2 = self._setup()
        result = vi.index_all()
        self.assertTrue(result["ok"])
        self.assertEqual(result["docs"], 2)
        self.assertEqual(result["chunks"], 2)


class HybridSearchTest(unittest.TestCase):
    def _setup(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cw-vec-"))
        db, mount, doc1, doc2 = make_db(self.tmpdir)
        store = VectorStore(db.path, dim=32)
        vi = VectorIndex(db, store, FakeEmbeddings(dim=32))
        vi.index_doc(doc1)
        vi.index_doc(doc2)
        return db, store, vi, doc1, doc2

    def tearDown(self) -> None:
        if getattr(self, "store", None) is not None:
            self.store.close()
        if getattr(self, "db", None) is not None:
            self.db.close()
        shutil.rmtree(getattr(self, "tmpdir", Path(".")), ignore_errors=True)

    def test_hybrid_routes(self) -> None:
        self.db, self.store, vi, doc1, _doc2 = self._setup()
        searcher = Searcher(self.db, vector_index=vi)
        hits = searcher.search("fcntl", topk=5)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].doc_id, doc1)
        self.assertIn(hits[0].route, ("hybrid", "jieba", "jieba_and", "vector"))

    def test_keyword_only_when_disabled(self) -> None:
        self.db, self.store, _vi, doc1, _doc2 = self._setup()
        searcher = Searcher(self.db)
        hits = searcher.search("fcntl", topk=5)
        self.assertEqual(hits[0].doc_id, doc1)
        self.assertNotIn(hits[0].route, ("vector", "hybrid"))


class P0GatingTest(unittest.TestCase):
    """P0：纯向量命中必须过阈值 + 词面证据 + 限条，且排在 BM25 之后。"""

    def _setup(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cw-p0-"))
        db, mount, doc1, doc2 = make_db(self.tmpdir)
        doc3 = db.upsert_doc(mount, "c.md", str(self.tmpdir / "c.md"), "菜单配置", "md", 1.0, 10, "h3", "indexed")
        db.set_chunks(doc3, ["系统菜单与权限配置说明。"], lambda s: s)
        doc4 = db.upsert_doc(mount, "d.md", str(self.tmpdir / "d.md"), "文件锁补充", "md", 1.0, 10, "h4", "indexed")
        db.set_chunks(doc4, ["文件锁的补充说明与示例。"], lambda s: s)
        ids = {(r["doc_id"], r["seq"]): r["chunk_id"] for r in db.query("SELECT chunk_id, doc_id, seq FROM chunks")}
        return db, {
            "doc1": doc1, "doc2": doc2, "doc3": doc3, "doc4": doc4, "ids": ids,
        }

    def tearDown(self):
        if getattr(self, "db", None):
            self.db.close()
        shutil.rmtree(getattr(self, "tmpdir", Path(".")), ignore_errors=True)

    def test_filters_no_lexical_and_low_sim(self):
        self.db, ctx = self._setup()
        ids = ctx["ids"]

        class StubVI:
            ready = True
            min_similarity = 0.62
            vector_only_max = 3
            require_lexical = True

            def search_ids(self, q, k):
                return [
                    (ids[(ctx["doc3"], 0)], 0.0),   # sim 1.0 但无词面证据
                    (ids[(ctx["doc2"], 0)], 0.0),   # Vue 文档，无词面证据
                    (ids[(ctx["doc1"], 0)], 0.4),   # 与 BM25 同文档 -> hybrid
                ]

        searcher = Searcher(self.db, vector_index=StubVI())
        hits = searcher.search("fcntl", topk=5)
        docs = [h.doc_id for h in hits]
        self.assertEqual(docs[0], ctx["doc1"])
        self.assertNotIn(ctx["doc3"], docs)
        self.assertNotIn(ctx["doc2"], docs)

    def test_vector_only_with_lexical_capped_and_last(self):
        self.db, ctx = self._setup()
        ids = ctx["ids"]

        class StubVI:
            ready = True
            min_similarity = 0.62
            vector_only_max = 1
            require_lexical = False

            def search_ids(self, q, k):
                return [
                    (ids[(ctx["doc4"], 0)], 0.2),   # 高相似，仅向量（关闭词面门槛）
                    (ids[(ctx["doc3"], 0)], 0.3),   # 次高，受 cap 限制丢弃
                    (ids[(ctx["doc1"], 0)], 0.4),   # BM25 同文档 -> hybrid
                ]

        searcher = Searcher(self.db, vector_index=StubVI())
        hits = searcher.search("fcntl", topk=5)
        self.assertEqual(hits[0].doc_id, ctx["doc1"])
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[-1].doc_id, ctx["doc4"])
        self.assertEqual(hits[-1].route, "vector")

if __name__ == "__main__":
    unittest.main()