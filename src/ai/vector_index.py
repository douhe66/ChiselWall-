"""向量索引（M7）：文档分块向量化、查询向量检索。"""
from __future__ import annotations

import logging
from typing import Optional

from ai.vector_backend import SqliteVecBackend, StoreBackend, VectorBackend

log = logging.getLogger("chiselwall.vector")


class VectorIndex:
    def __init__(self, db, store, embeddings, *, min_similarity: float = 0.62,
                 vector_only_max: int = 3, require_lexical: bool = True,
                 backend: Optional[VectorBackend] = None) -> None:
        self.db = db
        self.store = store
        self.embeddings = embeddings
        self.min_similarity = float(min_similarity)
        self.vector_only_max = int(vector_only_max)
        self.require_lexical = bool(require_lexical)
        if backend is not None:
            self.backend = backend
        elif hasattr(store, "db_path") and hasattr(store, "dim"):
            self.backend = SqliteVecBackend(store.db_path, getattr(store, "dim", 512), store=store)
        else:
            self.backend = StoreBackend(store)
        self._last_error = ""
        self._rebuilding = False

    @property
    def ready(self) -> bool:
        return bool(getattr(self.embeddings, "ready", False))

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def model_id(self) -> str:
        return str(getattr(self.embeddings, "model_id", "") or type(self.embeddings).__name__)

    @property
    def stale(self) -> bool:
        """索引元数据与当前模型/维度/度量是否不一致（不一致时停用向量）。"""
        meta = self.backend.get_meta()
        if not meta:
            return False  # 旧库无元数据：按当前配置继续用，重建时写入
        if meta.get("dim") and int(meta["dim"]) != int(self.backend.dim):
            return True
        if meta.get("model") and self.model_id and meta["model"] != self.model_id:
            return True
        if meta.get("metric") and meta["metric"] != self.backend.metric:
            return True
        return False

    def index_doc(self, doc_id: int) -> int:
        """为某文档的所有分块生成/更新向量；返回处理的分块数。"""
        if not self.ready:
            return 0
        if self.stale and not self._rebuilding:
            self._last_error = "向量模型/维度已变化，请重建向量索引"
            return 0
        try:
            rows = self.db.query(
                "SELECT chunk_id, text FROM chunks WHERE doc_id=? ORDER BY seq", (int(doc_id),)
            )
        except Exception:
            import threading

            log.exception(
                "index_doc 查询失败 doc=%s db=%s thread=%s",
                doc_id, self.db.path, threading.get_ident(),
            )
            raise
        if not rows:
            return 0
        ids = [int(r["chunk_id"]) for r in rows]
        texts = [str(r["text"] or "")[:2000] for r in rows]
        try:
            vecs = self.embeddings.embed(texts)
            self.backend.delete_chunks(ids)
            self.backend.upsert(list(zip(ids, vecs)))
            self._last_error = ""
            return len(ids)
        except Exception as exc:  # 向量化失败不影响关键词检索
            self._last_error = str(exc)
            log.exception("文档向量化失败 doc_id=%s", doc_id)
            return 0

    def index_all(self, progress=None) -> dict:
        """重建全部向量（后台任务）：换模型时先清空旧表，再写新元数据。"""
        if not self.ready:
            return {"ok": False, "error": "向量模型未就绪"}
        dim = int(getattr(self.embeddings, "dim", self.backend.dim) or self.backend.dim)
        try:
            self.backend.reset(dim)
            self._rebuilding = True
        except Exception as exc:
            self._last_error = str(exc)
            return {"ok": False, "error": "重建向量表失败: " + str(exc)}
        docs = self.db.query("SELECT doc_id FROM docs WHERE status='indexed'")
        total = 0
        try:
            for i, row in enumerate(docs):
                total += self.index_doc(int(row["doc_id"]))
                if progress and (i % 20 == 0):
                    progress(i + 1, len(docs))
            self.backend.set_meta(model=self.model_id, dim=dim, metric=self.backend.metric)
            self._last_error = ""
            return {"ok": True, "docs": len(docs), "chunks": total,
                    "model": self.model_id, "dim": dim, "metric": self.backend.metric,
                    "backend": self.backend.name}
        finally:
            self._rebuilding = False

    def search_ids(self, query: str, k: int = 20) -> list[tuple[int, float]]:
        if not self.ready:
            return []
        if self.stale:
            log.warning("向量索引与当前模型不一致，已停用向量检索，请重建")
            return []
        try:
            vec = self.embeddings.embed([query], query=True)[0]
            return self.backend.search(vec, k)
        except Exception as exc:
            self._last_error = str(exc)
            log.exception("查询向量化失败")
            return []
