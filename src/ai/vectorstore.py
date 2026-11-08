"""向量存储（M7）：sqlite-vec vec0 表，与索引库同文件、独立连接。"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("chiselwall.vector")


class VectorStore:
    """chunk 向量表：vec_chunks(chunk_id INTEGER PRIMARY KEY, embedding FLOAT[dim])。"""

    def __init__(self, db_path: Path, dim: int = 512) -> None:
        self.db_path = Path(db_path)
        self.dim = int(dim)
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        import sqlite_vec

        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception:
            log.exception("打开向量存储失败：%s", self.db_path)
            raise
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
            "chunk_id INTEGER PRIMARY KEY, embedding FLOAT[%d])" % self.dim
        )
        conn.commit()
        self._local.conn = conn
        return conn

    def upsert(self, items: list[tuple[int, list[float]]]) -> int:
        """写入/覆盖 chunk 向量；返回写入条数。"""
        if not items:
            return 0
        import sqlite_vec

        conn = self._conn()
        with conn:
            for chunk_id, vec in items:
                conn.execute("DELETE FROM vec_chunks WHERE chunk_id=?", (int(chunk_id),))
                conn.execute(
                    "INSERT INTO vec_chunks(chunk_id, embedding) VALUES(?, ?)",
                    (int(chunk_id), sqlite_vec.serialize_float32(vec)),
                )
        return len(items)

    def delete_chunks(self, chunk_ids: list[int]) -> None:
        if not chunk_ids:
            return
        conn = self._conn()
        with conn:
            for cid in chunk_ids:
                conn.execute("DELETE FROM vec_chunks WHERE chunk_id=?", (int(cid),))

    def search(self, vector: list[float], k: int = 20) -> list[tuple[int, float]]:
        """KNN：返回 [(chunk_id, distance)]（归一化向量下 distance 越小越相似）。"""
        import sqlite_vec

        conn = self._conn()
        rows = conn.execute(
            "SELECT chunk_id, distance FROM vec_chunks WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?",
            (sqlite_vec.serialize_float32(vector), int(k)),
        ).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows]

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def count(self) -> int:
        try:
            row = self._conn().execute("SELECT count(*) FROM vec_chunks").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0
