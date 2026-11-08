"""P3：向量后端接口 / 元数据 / 模型变更检测。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.embedding import FakeEmbeddings
from ai.vector_backend import SqliteVecBackend
from ai.vector_index import VectorIndex
from ai.vectorstore import VectorStore


class FakeDB:
    def query(self, _sql, _params=()):
        return []


class BackendTest(unittest.TestCase):
    def test_meta_and_similarity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = VectorStore(Path(tmp) / "v.db", dim=16)
            backend = SqliteVecBackend(Path(tmp) / "v.db", 16, store=store)
            backend.set_meta(model="m1", dim=16, metric="l2")
            meta = backend.get_meta()
            self.assertEqual(meta["model"], "m1")
            self.assertEqual(meta["dim"], "16")
            self.assertAlmostEqual(backend.similarity(0.0), 1.0)
            self.assertAlmostEqual(backend.similarity(2.0), 0.0)
            store.close()

    def test_stale_on_model_and_dim_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.db"
            store = VectorStore(path, dim=16)
            backend = SqliteVecBackend(path, 16, store=store)
            emb = FakeEmbeddings(16)
            index = VectorIndex(FakeDB(), store, emb, backend=backend)
            self.assertFalse(index.stale)  # 无元数据：旧库兼容
            backend.set_meta(model="old-model", dim=16)
            self.assertTrue(index.stale)
            backend.set_meta(model=emb.model_id, dim=16)
            self.assertFalse(index.stale)
            backend.set_meta(model=emb.model_id, dim=32)
            self.assertTrue(index.stale)
            store.close()

    def test_rebuild_resets_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.db"
            store = VectorStore(path, dim=16)
            backend = SqliteVecBackend(path, 16, store=store)
            emb = FakeEmbeddings(16)
            index = VectorIndex(FakeDB(), store, emb, backend=backend)
            backend.set_meta(model="old-model", dim=16)
            result = index.index_all()
            self.assertTrue(result["ok"])
            self.assertEqual(result["backend"], "sqlite-vec")
            self.assertEqual(backend.get_meta()["model"], emb.model_id)
            self.assertFalse(index.stale)
            store.close()


if __name__ == "__main__":
    unittest.main()
