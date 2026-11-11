"""索引库 IndexDB（设计 §5）：单文件 SQLite（chiselwall.db）。

存放位置：本机用户目录（默认 ~/.chiselwall/chiselwall.db），绝不放挂载目录内（红线②）。

表结构：
    mounts          挂载点（directory/obsidian/file）
    exclude_rules   用户排除规则（file/dir/glob，持久化）
    docs            文档元信息 + 状态（pending/indexed/error/excluded）+ (mtime,size,hash) 签名
    chunks          分块原文 text + jieba 影子列 text_jieba
    chunks_fts      FTS5：content（unicode61 主列，英文/数字）+ content_jieba（jieba 影子列），
                    rowid 与 chunks.chunk_id 对齐，bm25 排序（调研 03 §2.3①）
    chunks_trigram  FTS5 trigram 兜底表（P1，调研 03 §2.3③）：任意 ≥3 字子串命中

预留（§16，只登记不动工）：
    - vec0 向量表（sqlite-vec，512 维 int8）——M5 语义搜索可选开关
    - docs 排除位扩枚举（隐藏/标题可见/正文可见）——「只出标题」权限分级
    - 部门自定义词典更新 → 全量重灌 chunks_fts（调研 03 §2.3⑤）
    - trigram 开启与否按空间预算可配（建表成本低，查询路由见 searcher）

线程模型：单连接 + RLock 串行化（check_same_thread=False）。
M2 规模下索引写与检索读串行开销可忽略；WAL + busy_timeout 兜底并发场景。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS mounts(
  mount_id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK(kind IN ('directory','obsidian','file','siyuan')),
  path TEXT NOT NULL UNIQUE,
  added_at TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  sensitivity_ignored INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exclude_rules(
  rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK(kind IN ('file','dir','glob')),
  pattern TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups(
  group_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  sensitivity_ignored INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS group_members(
  member_id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
  mount_id INTEGER NOT NULL REFERENCES mounts(mount_id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_members_group ON group_members(group_id);
CREATE TABLE IF NOT EXISTS docs(
  doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
  mount_id INTEGER NOT NULL REFERENCES mounts(mount_id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL,
  abs_path TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT '',
  mtime REAL,
  size INTEGER,
  hash TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','indexed','error','excluded')),
  share_state TEXT NOT NULL DEFAULT 'shared' CHECK(share_state IN ('shared','private','hidden')),
  error TEXT,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  truncated INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT,
  UNIQUE(mount_id, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_docs_status ON docs(status);
CREATE INDEX IF NOT EXISTS idx_docs_mount ON docs(mount_id, rel_path);
CREATE TABLE IF NOT EXISTS chunks(
  chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  content, content_jieba,
  tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_trigram USING fts5(
  content,
  tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS sensitivity_rules(
  rule_id TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK(source IN ('builtin','custom')),
  label TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  severity TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN ('high','medium','low')),
  kind TEXT NOT NULL CHECK(kind IN ('word','regex')),
  scope TEXT NOT NULL DEFAULT 'all' CHECK(scope IN ('all','path')),
  validator TEXT NOT NULL DEFAULT '',
  pattern TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sens_rules_source ON sensitivity_rules(source, enabled);
CREATE TABLE IF NOT EXISTS sensitivity_hits(
  doc_id INTEGER NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  rule_id TEXT NOT NULL REFERENCES sensitivity_rules(rule_id) ON DELETE CASCADE,
  count INTEGER NOT NULL DEFAULT 1,
  excerpt TEXT NOT NULL DEFAULT '',
  severity TEXT NOT NULL DEFAULT 'medium',
  PRIMARY KEY(doc_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_sens_hits_doc ON sensitivity_hits(doc_id);
CREATE TABLE IF NOT EXISTS doc_sensitivity(
  doc_id INTEGER PRIMARY KEY REFERENCES docs(doc_id) ON DELETE CASCADE,
  hit_signature TEXT NOT NULL DEFAULT '',
  ack_signature TEXT NOT NULL DEFAULT '',
  ignored INTEGER NOT NULL DEFAULT 0,
  scanned_at TEXT
);
CREATE TABLE IF NOT EXISTS sensitivity_doc_rule_ignores(
  doc_id INTEGER NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  rule_id TEXT NOT NULL REFERENCES sensitivity_rules(rule_id) ON DELETE CASCADE,
  PRIMARY KEY(doc_id, rule_id)
);
"""


log = logging.getLogger("chiselwall.db")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")


@dataclass(frozen=True)
class MountRecord:
    mount_id: int
    kind: str
    path: str
    added_at: str
    enabled: int = 1
    sensitivity_ignored: int = 0


@dataclass(frozen=True)
class DocRecord:
    doc_id: int
    mount_id: int
    rel_path: str
    abs_path: str
    title: str
    format: str
    mtime: Optional[float]
    size: Optional[int]
    hash: Optional[str]
    status: str
    error: Optional[str]
    chunk_count: int
    truncated: bool
    updated_at: Optional[str]
    share_state: str = "shared"


class IndexDB:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA temp_store=MEMORY")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """旧版本 schema 迁移（幂等，每次打开库执行一次）。

        1) M2-FIX2：早期 chunks 表带 text_jieba NOT NULL 物理列；影子列已移入
           FTS 表，新代码不再写该列——旧库若不删列，set_chunks 触发 IntegrityError。
        2) M3-1：mounts 表旧 CHECK 约束不含 kind='siyuan'——SQLite 无法修改
           CHECK，需重建 mounts 表（保留行数据与自增 id）。
        要求 SQLite ≥ 3.35（Python 3.10+ 自带版本均满足）。
        """
        doc_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(docs)").fetchall()}
        if "share_state" not in doc_cols:
            self._conn.execute("ALTER TABLE docs ADD COLUMN share_state TEXT NOT NULL DEFAULT 'shared'")
            self._conn.commit()
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "text_jieba" in cols:
            self._conn.execute("ALTER TABLE chunks DROP COLUMN text_jieba")
            log.info("migrated legacy schema: dropped chunks.text_jieba column")
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='mounts'"
        ).fetchone()
        mounts_sql = row["sql"] if row else ""
        if mounts_sql and "siyuan" not in mounts_sql:
            log.info("migrating mounts table: allow kind='siyuan'")
            self._conn.execute("PRAGMA foreign_keys=OFF")
            self._conn.commit()
            self._conn.executescript(
                "BEGIN;"
                "CREATE TABLE mounts_new("
                "  mount_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL,"
                "  path TEXT NOT NULL UNIQUE,"
                "  added_at TEXT NOT NULL"
                ");"
                "INSERT INTO mounts_new(mount_id, kind, path, added_at)"
                "  SELECT mount_id, kind, path, added_at FROM mounts;"
                "DROP TABLE mounts;"
                "ALTER TABLE mounts_new RENAME TO mounts;"
                "COMMIT;"
            )
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.commit()
            log.info("mounts table rebuilt")
        mount_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(mounts)").fetchall()}
        if "enabled" not in mount_cols:
            self._conn.execute("ALTER TABLE mounts ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            self._conn.commit()
        mount_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(mounts)").fetchall()}
        if "sensitivity_ignored" not in mount_cols:
            self._conn.execute("ALTER TABLE mounts ADD COLUMN sensitivity_ignored INTEGER NOT NULL DEFAULT 0")
            log.info("migrated mounts.sensitivity_ignored")
        group_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(groups)").fetchall()}
        if "sensitivity_ignored" not in group_cols:
            self._conn.execute("ALTER TABLE groups ADD COLUMN sensitivity_ignored INTEGER NOT NULL DEFAULT 0")
            log.info("migrated groups.sensitivity_ignored")
        rule_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(sensitivity_rules)").fetchall()}
        if "validator" not in rule_cols:
            self._conn.execute("ALTER TABLE sensitivity_rules ADD COLUMN validator TEXT NOT NULL DEFAULT ''")
            log.info("migrated sensitivity_rules.validator")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self):
        """批量写事务：退出时统一提交，异常回滚。"""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # ---- 通用查询（searcher 与 stats 使用） ----
    def query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ---- 挂载 ----
    def add_mount(self, kind: str, path: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO mounts(kind, path, added_at) VALUES(?,?,?)", (kind, path, _now())
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_mounts(self) -> list[MountRecord]:
        rows = self.query("SELECT * FROM mounts ORDER BY mount_id")
        return [MountRecord(**dict(r)) for r in rows]

    def get_mount(self, mount_id: int) -> Optional[MountRecord]:
        rows = self.query("SELECT * FROM mounts WHERE mount_id=?", (mount_id,))
        return MountRecord(**dict(rows[0])) if rows else None

    def mount_by_path(self, path: str) -> Optional[MountRecord]:
        rows = self.query("SELECT * FROM mounts WHERE path=?", (path,))
        return MountRecord(**dict(rows[0])) if rows else None

    def remove_mount(self, mount_id: int) -> None:
        """删除挂载：先摘除 FTS 行，再删挂载（docs/chunks 级联删除）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.chunk_id FROM chunks c JOIN docs d ON d.doc_id = c.doc_id "
                "WHERE d.mount_id=?",
                (mount_id,),
            ).fetchall()
            for r in rows:
                self._conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (r["chunk_id"],))
                self._conn.execute("DELETE FROM chunks_trigram WHERE rowid=?", (r["chunk_id"],))
            self._conn.execute("DELETE FROM mounts WHERE mount_id=?", (mount_id,))
            self._conn.commit()

    # ---- 排除规则 ----
    def add_exclude_rule(self, kind: str, pattern: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO exclude_rules(kind, pattern, created_at) VALUES(?,?,?)",
                (kind, pattern, _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_exclude_rules(self) -> list[tuple[int, str, str]]:
        rows = self.query("SELECT rule_id, kind, pattern FROM exclude_rules ORDER BY rule_id")
        return [(r["rule_id"], r["kind"], r["pattern"]) for r in rows]

    def remove_exclude_rule(self, rule_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM exclude_rules WHERE rule_id=?", (rule_id,))
            self._conn.commit()

    # ---- 文档 ----
    def upsert_doc(
        self,
        mount_id: int,
        rel_path: str,
        abs_path: str,
        title: str,
        format: str,
        mtime: Optional[float],
        size: Optional[int],
        hash: Optional[str],
        status: str,
        error: Optional[str] = None,
    ) -> int:
        with self._lock:
            self._conn.execute(
                """INSERT INTO docs(mount_id, rel_path, abs_path, title, format,
                                    mtime, size, hash, status, error, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(mount_id, rel_path) DO UPDATE SET
                     abs_path=excluded.abs_path, title=excluded.title, format=excluded.format,
                     mtime=excluded.mtime, size=excluded.size, hash=excluded.hash,
                     status=excluded.status, error=excluded.error, updated_at=excluded.updated_at""",
                (mount_id, rel_path, abs_path, title, format, mtime, size, hash, status, error, _now()),
            )
            row = self._conn.execute(
                "SELECT doc_id FROM docs WHERE mount_id=? AND rel_path=?", (mount_id, rel_path)
            ).fetchone()
            self._conn.commit()
            return int(row["doc_id"])

    def get_doc(self, mount_id: int, rel_path: str) -> Optional[DocRecord]:
        rows = self.query(
            "SELECT * FROM docs WHERE mount_id=? AND rel_path=?", (mount_id, rel_path)
        )
        return DocRecord(**dict(rows[0])) if rows else None

    def get_doc_by_id(self, doc_id: int) -> Optional[DocRecord]:
        rows = self.query("SELECT * FROM docs WHERE doc_id=?", (doc_id,))
        return DocRecord(**dict(rows[0])) if rows else None

    def set_doc_status(
        self,
        doc_id: int,
        status: str,
        *,
        error: Optional[str] = None,
        chunk_count: Optional[int] = None,
        truncated: Optional[bool] = None,
    ) -> None:
        sets = ["status=?", "error=?", "updated_at=?"]
        params: list = [status, error, _now()]
        if chunk_count is not None:
            sets.append("chunk_count=?")
            params.append(chunk_count)
        if truncated is not None:
            sets.append("truncated=?")
            params.append(int(truncated))
        sql = "UPDATE docs SET " + ", ".join(sets) + " WHERE doc_id=?"  # 仅内部固定模板拼接
        with self._lock:
            self._conn.execute(sql, tuple(params) + (doc_id,))
            self._conn.commit()

    def delete_doc(self, mount_id: int, rel_path: str) -> None:
        """删除文档及其全部分块与 FTS 行。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT doc_id FROM docs WHERE mount_id=? AND rel_path=?", (mount_id, rel_path)
            ).fetchone()
            if row:
                self._delete_chunks_locked(int(row["doc_id"]))
                self._conn.execute("DELETE FROM docs WHERE doc_id=?", (row["doc_id"],))
            self._conn.commit()

    def delete_docs_under(self, mount_id: int, rel_prefix: str) -> int:
        """删除某目录前缀下全部文档（目录删除/移走事件）。返回删除数。"""
        prefix = rel_prefix.rstrip("/")
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id FROM docs WHERE mount_id=? AND (rel_path=? OR rel_path LIKE ?)",
                (mount_id, prefix, prefix + "/%"),
            ).fetchall()
            for r in rows:
                self._delete_chunks_locked(int(r["doc_id"]))
                self._conn.execute("DELETE FROM docs WHERE doc_id=?", (r["doc_id"],))
            self._conn.commit()
            return len(rows)

    def _delete_chunks_locked(self, doc_id: int) -> None:
        """摘除 doc 的全部分块（chunks + 两张 FTS 表）。须在锁内调用。"""
        rows = self._conn.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)
        ).fetchall()
        for r in rows:
            self._conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (r["chunk_id"],))
            self._conn.execute("DELETE FROM chunks_trigram WHERE rowid=?", (r["chunk_id"],))
        self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    def clear_chunks(self, doc_id: int) -> None:
        """对外公开的 chunk 摘除（红线⑤：文档被排除后立即摘除）。"""
        with self._lock:
            self._delete_chunks_locked(doc_id)
            self._conn.commit()

    def set_chunks(
        self,
        doc_id: int,
        chunks: list[str],
        tokenize_fn: Callable[[str], str],
        *,
        trigram: bool = False,
        trigram_max_chars: int = 100_000,
        batch_size: int = 200,
    ) -> int:
        """重建分块与 FTS 行（先摘除旧行再分批写入）。返回 chunk 数。

        F1 修复（资源控制）：分批提交、批间释放锁——大文档（上千 chunk）重索引时
        检索可穿插执行，索引期检索延迟保持毫秒级；jieba 分词在锁外完成。
        F2 修复（trigram 兜底表）：默认关闭；开启时仅对总字符数 ≤ trigram_max_chars
        的小文档写入（trigram 索引体积约为原文数倍，大文档不划算，设计 §5 已同步）。
        """
        with self._lock:
            self._delete_chunks_locked(doc_id)
            self._conn.commit()
        # 锁外做总长判断与分词（分词是主要耗时）
        write_trigram = trigram and sum(len(c) for c in chunks) <= trigram_max_chars
        for seq, text in enumerate(chunks):
            jt = tokenize_fn(text)
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO chunks(doc_id, seq, text) VALUES(?,?,?)", (doc_id, seq, text)
                )
                cid = int(cur.lastrowid)
                self._conn.execute(
                    "INSERT INTO chunks_fts(rowid, content, content_jieba) VALUES(?,?,?)",
                    (cid, text, jt),
                )
                if write_trigram:
                    self._conn.execute(
                        "INSERT INTO chunks_trigram(rowid, content) VALUES(?,?)", (cid, text)
                    )
                if (seq + 1) % batch_size == 0:
                    self._conn.commit()
        with self._lock:
            self._conn.commit()
        return len(chunks)

    def get_chunk_texts(self, doc_id: int) -> list[str]:
        rows = self.query("SELECT text FROM chunks WHERE doc_id=? ORDER BY seq", (doc_id,))
        return [r["text"] for r in rows]

    def doc_paths(self, mount_id: int) -> list[str]:
        rows = self.query("SELECT rel_path FROM docs WHERE mount_id=?", (mount_id,))
        return [r["rel_path"] for r in rows]

    # ---- 笔记分组（设计 §15.6；M3-2） ----
    def add_group(self, name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO groups(name, created_at) VALUES(?,?)", (name, _now())
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_groups(self) -> list[dict]:
        rows = self.query(
            "SELECT g.group_id, g.name, g.created_at, g.sensitivity_ignored, "
            "COUNT(m.member_id) AS members FROM groups g "
            "LEFT JOIN group_members m ON m.group_id = g.group_id "
            "GROUP BY g.group_id ORDER BY g.group_id"
        )
        return [dict(r) for r in rows]

    def get_group(self, group_id: int) -> Optional[dict]:
        rows = self.query("SELECT * FROM groups WHERE group_id=?", (group_id,))
        return dict(rows[0]) if rows else None

    def remove_group(self, group_id: int) -> None:
        """删除组：仅删除分组关系（成员记录级联），不删除任何排除规则（GR-07 裁决）。"""
        with self._lock:
            self._conn.execute("DELETE FROM groups WHERE group_id=?", (group_id,))
            self._conn.commit()

    def add_group_member(self, group_id: int, mount_id: int, rel_path: str = "") -> int:
        """归组：rel_path='' 表示整个挂载；否则为挂载内具体文档相对路径。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO group_members(group_id, mount_id, rel_path) VALUES(?,?,?)",
                (group_id, mount_id, rel_path),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_group_members(self, group_id: int) -> list[dict]:
        rows = self.query(
            "SELECT m.member_id, m.group_id, m.mount_id, m.rel_path, mt.kind, mt.path "
            "FROM group_members m JOIN mounts mt ON mt.mount_id = m.mount_id "
            "WHERE m.group_id=? ORDER BY m.member_id",
            (group_id,),
        )
        return [dict(r) for r in rows]

    def remove_group_member(self, member_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM group_members WHERE member_id=?", (member_id,))
            self._conn.commit()

    def group_doc_count(self, group_id: int) -> int:
        """组内已索引文档数（整挂载成员 + 具体文档成员，去重计数）。"""
        rows = self.query(
            "SELECT COUNT(DISTINCT d.doc_id) AS n FROM docs d "
            "WHERE d.status='indexed' AND EXISTS ("
            "  SELECT 1 FROM group_members m WHERE m.group_id=? AND m.mount_id=d.mount_id "
            "  AND (m.rel_path='' OR m.rel_path=d.rel_path))",
            (group_id,),
        )
        return int(rows[0]["n"])

    # ---- 统计与列表 ----
    def stats(self) -> dict:
        rows = self.query("SELECT status, COUNT(*) AS n FROM docs GROUP BY status")
        by = {r["status"]: r["n"] for r in rows}
        mounts = self.query("SELECT COUNT(*) AS n FROM mounts")[0]["n"]
        chunks = self.query("SELECT COUNT(*) AS n FROM chunks")[0]["n"]
        # 排除构成分类（M2-FIX4：用户删除规则后仍显示「排除 2」易误解——
        # 拆分为 用户规则/默认规则/不支持格式/孪生去重 四类，UI 明细展示）
        cats = {"user_rule": 0, "default": 0, "unsupported_format": 0, "duplicate_md_twin": 0}
        for r in self.query("SELECT error FROM docs WHERE status='excluded' AND error IS NOT NULL"):
            e = r["error"] or ""
            if e.startswith("用户排除规则"):
                cats["user_rule"] += 1
            elif e.startswith("duplicate_md_twin"):
                cats["duplicate_md_twin"] += 1
            elif e.startswith("不支持的格式"):
                cats["unsupported_format"] += 1
            else:
                cats["default"] += 1
        return {
            "mounts": mounts,
            "indexed": by.get("indexed", 0),
            "pending": by.get("pending", 0),
            "error": by.get("error", 0),
            "excluded": by.get("excluded", 0),
            "chunks": chunks,
            "excluded_by_reason": cats,
        }

    def failed_docs(self, limit: int = 100) -> list[dict]:
        rows = self.query(
            "SELECT doc_id, mount_id, rel_path, title, format, error, updated_at "
            "FROM docs WHERE status='error' ORDER BY doc_id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def excluded_docs(self, limit: int = 100) -> list[dict]:
        rows = self.query(
            "SELECT doc_id, mount_id, rel_path, title, format, error, updated_at "
            "FROM docs WHERE status='excluded' ORDER BY doc_id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def set_mount_enabled(self, mount_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE mounts SET enabled=? WHERE mount_id=?", (1 if enabled else 0, int(mount_id)))
            self._conn.commit()

    def set_doc_share(self, doc_id: int, state: str) -> None:
        if state not in ("shared", "private", "hidden"):
            raise ValueError("share_state 须为 shared/private/hidden")
        with self._lock:
            self._conn.execute("UPDATE docs SET share_state=? WHERE doc_id=?", (state, int(doc_id)))
            self._conn.commit()

    # ---- 敏感词规则 / 命中 / 确认（L0+L1） ----
    def list_sensitivity_rules(self) -> list[dict]:
        rows = self.query(
            "SELECT rule_id, source, label, category, severity, kind, scope, pattern, "
            "enabled, version, validator, created_at, updated_at FROM sensitivity_rules "
            "ORDER BY CASE source WHEN 'builtin' THEN 0 ELSE 1 END, rowid"
        )
        return [dict(r) for r in rows]

    def get_sensitivity_rule(self, rule_id: str) -> Optional[dict]:
        rows = self.query("SELECT * FROM sensitivity_rules WHERE rule_id=?", (rule_id,))
        return dict(rows[0]) if rows else None

    def upsert_builtin_sensitivity_rule(
        self, rule_id: str, label: str, category: str, severity: str,
        kind: str, scope: str, pattern: str, version: int, validator: str = "",
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sensitivity_rules(rule_id, source, label, category, severity, "
                "kind, scope, validator, pattern, enabled, version, created_at, updated_at) "
                "VALUES(?, 'builtin', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(rule_id) DO UPDATE SET label=excluded.label, "
                "category=excluded.category, severity=excluded.severity, kind=excluded.kind, "
                "scope=excluded.scope, validator=excluded.validator, pattern=excluded.pattern, "
                "version=excluded.version, updated_at=excluded.updated_at",
                (rule_id, label, category, severity, kind, scope, validator, pattern, int(version), now, now),
            )
            self._conn.commit()

    def add_custom_sensitivity_rule(
        self, label: str, pattern: str, category: str = "", severity: str = "medium",
    ) -> dict:
        if severity not in ("high", "medium", "low"):
            raise ValueError("severity 须为 high/medium/low")
        label = (label or "").strip()
        pattern = (pattern or "").strip()
        if not label:
            raise ValueError("规则名称不能为空")
        if not pattern:
            raise ValueError("敏感词不能为空")
        if len(pattern) > 200:
            raise ValueError("敏感词过长（最多 200 字）")
        rule_id = "custom." + uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sensitivity_rules(rule_id, source, label, category, severity, "
                "kind, scope, pattern, enabled, version, created_at, updated_at) "
                "VALUES(?, 'custom', ?, ?, ?, 'word', 'all', ?, 1, 1, ?, ?)",
                (rule_id, label, category or "自定义", severity, pattern, now, now),
            )
            self._conn.commit()
        return self.get_sensitivity_rule(rule_id) or {}

    def set_sensitivity_rule_enabled(self, rule_id: str, enabled: bool) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sensitivity_rules SET enabled=?, updated_at=? WHERE rule_id=?",
                (1 if enabled else 0, _now(), rule_id),
            )
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError("规则不存在")
        return self.get_sensitivity_rule(rule_id) or {}

    def delete_custom_sensitivity_rule(self, rule_id: str) -> None:
        rule = self.get_sensitivity_rule(rule_id)
        if rule is None:
            raise LookupError("规则不存在")
        if rule["source"] != "custom":
            raise ValueError("内置敏感词不可删除，只能屏蔽")
        with self._lock:
            self._conn.execute("DELETE FROM sensitivity_rules WHERE rule_id=?", (rule_id,))
            self._conn.commit()

    def set_mount_sensitivity_ignored(self, mount_id: int, ignored: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE mounts SET sensitivity_ignored=? WHERE mount_id=?",
                (1 if ignored else 0, int(mount_id)),
            )
            self._conn.commit()

    def set_group_sensitivity_ignored(self, group_id: int, ignored: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE groups SET sensitivity_ignored=? WHERE group_id=?",
                (1 if ignored else 0, int(group_id)),
            )
            self._conn.commit()

    def get_doc_sensitivity(self, doc_id: int) -> Optional[dict]:
        rows = self.query("SELECT * FROM doc_sensitivity WHERE doc_id=?", (doc_id,))
        return dict(rows[0]) if rows else None

    def set_doc_sensitivity_hits(self, doc_id: int, hit_signature: str, hits: list[dict]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sensitivity_hits WHERE doc_id=?", (doc_id,))
            self._conn.executemany(
                "INSERT INTO sensitivity_hits(doc_id, rule_id, count, excerpt, severity) "
                "VALUES(?,?,?,?,?)",
                [
                    (doc_id, h["rule_id"], int(h["count"]), h.get("excerpt", ""), h.get("severity", "medium"))
                    for h in hits
                ],
            )
            self._conn.execute(
                "INSERT INTO doc_sensitivity(doc_id, hit_signature, ack_signature, ignored, scanned_at) "
                "VALUES(?,?, '', 0, ?) ON CONFLICT(doc_id) DO UPDATE SET "
                "hit_signature=excluded.hit_signature, scanned_at=excluded.scanned_at",
                (doc_id, hit_signature, _now()),
            )
            self._conn.commit()

    def ack_doc_sensitivity(self, doc_id: int, signature: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO doc_sensitivity(doc_id, hit_signature, ack_signature, ignored, scanned_at) "
                "VALUES(?, '', ?, 0, ?) ON CONFLICT(doc_id) DO UPDATE SET ack_signature=excluded.ack_signature",
                (doc_id, signature, _now()),
            )
            self._conn.commit()

    def set_doc_sensitivity_ignored(self, doc_id: int, ignored: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO doc_sensitivity(doc_id, hit_signature, ack_signature, ignored, scanned_at) "
                "VALUES(?, '', '', ?, NULL) ON CONFLICT(doc_id) DO UPDATE SET ignored=excluded.ignored",
                (doc_id, 1 if ignored else 0),
            )
            self._conn.commit()

    def sensitivity_hits_for_doc(self, doc_id: int) -> list[dict]:
        rows = self.query(
            "SELECT h.rule_id, h.count, h.excerpt, h.severity, r.label, r.category, "
            "r.source, r.kind, r.scope FROM sensitivity_hits h "
            "JOIN sensitivity_rules r ON r.rule_id=h.rule_id WHERE h.doc_id=? "
            "ORDER BY CASE h.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, h.count DESC",
            (doc_id,),
        )
        return [dict(r) for r in rows]

    def sensitivity_hits_for_docs(self, doc_ids: list[int]) -> dict[int, list[dict]]:
        if not doc_ids:
            return {}
        marks = ",".join("?" for _ in doc_ids)
        rows = self.query(
            "SELECT h.doc_id, h.rule_id, h.count, h.excerpt, h.severity, r.label, r.category "
            "FROM sensitivity_hits h JOIN sensitivity_rules r ON r.rule_id=h.rule_id "
            "WHERE h.doc_id IN (" + marks + ") ORDER BY h.doc_id, h.severity, h.count DESC",
            doc_ids,
        )
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(int(r["doc_id"]), []).append(dict(r))
        return out

    def sensitivity_doc_rule_ignores(self, doc_id: int) -> set[str]:
        rows = self.query("SELECT rule_id FROM sensitivity_doc_rule_ignores WHERE doc_id=?", (doc_id,))
        return {str(r["rule_id"]) for r in rows}

    def add_doc_sensitivity_rule_ignore(self, doc_id: int, rule_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sensitivity_doc_rule_ignores(doc_id, rule_id) VALUES(?,?)",
                (doc_id, rule_id),
            )
            self._conn.commit()

    def remove_doc_sensitivity_rule_ignore(self, doc_id: int, rule_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM sensitivity_doc_rule_ignores WHERE doc_id=? AND rule_id=?",
                (doc_id, rule_id),
            )
            self._conn.commit()

    def list_docs(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = 500,
        mount_id: Optional[int] = None,
        group_id: Optional[int] = None,
    ) -> list[dict]:
        """文档列表（limit=None 表示全量；支持按挂载/分组过滤，M3-2）。"""
        cols = ("doc_id, mount_id, rel_path, abs_path, title, format, status, share_state, "
                "chunk_count, mtime, size, hash")
        sql = "SELECT " + cols + " FROM docs d"
        where: list[str] = []
        params: list = []
        if status is not None:
            where.append("d.status=?")
            params.append(status)
        if mount_id is not None:
            where.append("d.mount_id=?")
            params.append(mount_id)
        if group_id is not None:
            # 组内文档：整挂载成员（rel_path=''）或具体文档成员
            where.append(
                "EXISTS (SELECT 1 FROM group_members m WHERE m.group_id=? "
                "AND m.mount_id=d.mount_id AND (m.rel_path='' OR m.rel_path=d.rel_path))"
            )
            params.append(group_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY d.doc_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.query(sql, tuple(params))
        return [dict(r) for r in rows]
