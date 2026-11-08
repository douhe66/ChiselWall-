"""P3 向量后端接口：引擎可替换、度量声明、元数据（模型/维度/度量）。

当前实现：sqlite-vec（L2 距离，向量已归一化 -> 相似度 1 - d/2）。
未来可加 FAISS/pgvector 等，只需实现同一接口。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from ai.vectorstore import VectorStore

log = logging.getLogger("chiselwall.vector")


class VectorBackend:
    """向量后端最小接口。"""

    name = "base"
    metric = "l2"

    def __init__(self, dim: int) -> None:
        self.dim = int(dim)

    def upsert(self, items: list[tuple[int, list[float]]]) -> int:
        raise NotImplementedError

    def delete_chunks(self, chunk_ids: list[int]) -> None:
        raise NotImplementedError

    def search(self, vector: list[float], k: int = 20) -> list[tuple[int, float]]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def reset(self, dim: int) -> None:
        """清空并重建为指定维度（换模型时调用）。"""
        raise NotImplementedError

    def get_meta(self) -> dict:
        return {}

    def set_meta(self, *, model: str, dim: int, metric: Optional[str] = None) -> None:
        return None

    def similarity(self, distance: float) -> float:
        """距离 -> 相似度；默认按归一化向量的 L2：1 - d/2。"""
        return max(0.0, min(1.0, 1.0 - float(distance) / 2.0))


class StoreBackend(VectorBackend):
    """兼容测试/自定义 store：直接委托，不持久化元数据。"""

    name = "store"

    def __init__(self, store) -> None:
        super().__init__(getattr(store, "dim", 512))
        self.store = store

    def upsert(self, items):
        return self.store.upsert(items)

    def delete_chunks(self, chunk_ids):
        return self.store.delete_chunks(chunk_ids)

    def search(self, vector, k=20):
        return self.store.search(vector, k)

    def count(self):
        return self.store.count()

    def close(self):
        return self.store.close()

    def reset(self, dim):
        self.dim = int(dim)
        if hasattr(self.store, "dim"):
            self.store.dim = int(dim)


class SqliteVecBackend(VectorBackend):
    name = "sqlite-vec"
    metric = "l2"

    def __init__(self, db_path, dim: int = 512, store: Optional[VectorStore] = None) -> None:
        super().__init__(dim)
        self.store = store or VectorStore(Path(db_path), dim)
        self._ensure_meta()

    def _ensure_meta(self) -> None:
        conn = self.store._conn()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vector_meta("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()

    def upsert(self, items):
        return self.store.upsert(items)

    def delete_chunks(self, chunk_ids):
        return self.store.delete_chunks(chunk_ids)

    def search(self, vector, k=20):
        return self.store.search(vector, k)

    def count(self):
        return self.store.count()

    def close(self):
        return self.store.close()

    def reset(self, dim: int) -> None:
        conn = self.store._conn()
        conn.execute("DROP TABLE IF EXISTS vec_chunks")
        conn.commit()
        try:
            conn.close()
        finally:
            self.store._local.conn = None  # 重新建表按新维度
        self.dim = int(dim)
        self.store.dim = int(dim)
        self._ensure_meta()

    def get_meta(self) -> dict:
        try:
            rows = self.store._conn().execute("SELECT key, value FROM vector_meta").fetchall()
        except Exception:
            return {}
        return {str(r[0]): str(r[1]) for r in rows}

    def set_meta(self, *, model: str, dim: int, metric: Optional[str] = None) -> None:
        values = {
            "model": model,
            "dim": str(int(dim)),
            "metric": metric or self.metric,
            "backend": self.name,
            "updated_at": str(int(time.time())),
        }
        conn = self.store._conn()
        with conn:
            for key, value in values.items():
                conn.execute("INSERT OR REPLACE INTO vector_meta(key, value) VALUES(?, ?)", (key, value))


def build_backend(name: str, db_path, dim: int = 512, store: Optional[VectorStore] = None) -> VectorBackend:
    key = (name or "sqlite-vec").strip().lower()
    if key in ("sqlite-vec", "sqlite_vec", "vec0"):
        return SqliteVecBackend(db_path, dim, store=store)
    raise ValueError("不支持的向量后端: %s" % name)
