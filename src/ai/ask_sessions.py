"""随手问会话：会话列表 + 逐轮 turns（问题/回答/检索结果持久化）。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


class AskSessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS ask_sessions("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
                "CREATE TABLE IF NOT EXISTS ask_turns("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,"
                "content TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);"
                "CREATE INDEX IF NOT EXISTS idx_ask_turns_sid ON ask_turns(session_id, id);"
            )
            self._conn.commit()

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, (SELECT count(*) FROM ask_turns t WHERE t.session_id=s.id) AS n "
                "FROM ask_sessions s ORDER BY s.updated_at DESC"
            ).fetchall()
        return [dict(self._row(r), message_count=int(r["n"])) for r in rows]

    def create(self, title: str = "") -> dict:
        now = time.time()
        sid = uuid.uuid4().hex[:12]
        title = (title or "").strip()[:60] or "新会话"
        with self._lock:
            self._conn.execute(
                "INSERT INTO ask_sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (sid, title, now, now),
            )
            self._conn.commit()
        return {"id": sid, "title": title, "created_at": now, "updated_at": now}

    def rename(self, sid: str, title: str) -> bool:
        title = (title or "").strip()[:60]
        if not title:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE ask_sessions SET title=?, updated_at=? WHERE id=?", (title, time.time(), sid)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def delete(self, sid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM ask_sessions WHERE id=?", (sid,))
            self._conn.execute("DELETE FROM ask_turns WHERE session_id=?", (sid,))
            self._conn.commit()
        return cur.rowcount > 0

    def get(self, sid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM ask_sessions WHERE id=?", (sid,)).fetchone()
        return self._row(row) if row else None

    def turns(self, sid: str, limit: int = 300) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ask_turns WHERE session_id=? ORDER BY id LIMIT ?", (sid, int(limit))
            ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"]) if r["payload"] else {}
            except ValueError:
                payload = {}
            out.append({
                "id": int(r["id"]), "role": r["role"], "content": r["content"],
                "payload": payload, "created_at": float(r["created_at"]),
            })
        return out

    def append(self, sid: str, role: str, content: str, payload: Optional[dict] = None) -> dict:
        now = time.time()
        raw = json.dumps(payload or {}, ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO ask_turns(session_id,role,content,payload,created_at) VALUES(?,?,?,?,?)",
                (sid, role, content or "", raw, now),
            )
            self._conn.execute("UPDATE ask_sessions SET updated_at=? WHERE id=?", (now, sid))
            self._conn.commit()
        return {"id": int(cur.lastrowid), "role": role, "content": content or "", "payload": payload or {}, "created_at": now}

    def update_payload(self, turn_id: int, payload: dict) -> bool:
        raw = json.dumps(payload or {}, ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute("UPDATE ask_turns SET payload=? WHERE id=?", (raw, int(turn_id)))
            self._conn.commit()
        return cur.rowcount > 0

    def touch(self, sid: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE ask_sessions SET updated_at=? WHERE id=?", (time.time(), sid))
            self._conn.commit()

    def _row(self, row: sqlite3.Row) -> dict:
        return {"id": row["id"], "title": row["title"],
                "created_at": float(row["created_at"]), "updated_at": float(row["updated_at"])}

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
