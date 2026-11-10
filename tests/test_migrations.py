"""旧库迁移：mounts 重建后 enabled 仍存在；docs 增加 share_state。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB


class MigrationTest(unittest.TestCase):
    def test_old_mounts_and_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                "CREATE TABLE mounts("
                " mount_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " kind TEXT NOT NULL CHECK(kind IN ('directory','obsidian','file')),"
                " path TEXT NOT NULL UNIQUE, added_at TEXT NOT NULL);"
                "INSERT INTO mounts(kind,path,added_at) VALUES('directory','/tmp/a','2026-01-01');"
                "CREATE TABLE docs("
                " doc_id INTEGER PRIMARY KEY AUTOINCREMENT, mount_id INTEGER NOT NULL,"
                " rel_path TEXT NOT NULL, abs_path TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
                " format TEXT NOT NULL DEFAULT '', mtime REAL, size INTEGER, hash TEXT,"
                " status TEXT NOT NULL DEFAULT 'pending', error TEXT,"
                " chunk_count INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0,"
                " updated_at TEXT, UNIQUE(mount_id, rel_path));"
            )
            conn.commit()
            conn.close()
            db = IndexDB(path)
            try:
                mounts = db.list_mounts()
                self.assertEqual(len(mounts), 1)
                self.assertEqual(int(mounts[0].enabled), 1)
                doc_id = db.upsert_doc(
                    1, "a.md", "/tmp/a.md", "A", "md", 1, 1, "h", "indexed"
                )
                self.assertEqual(db.get_doc_by_id(doc_id).share_state, "shared")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
