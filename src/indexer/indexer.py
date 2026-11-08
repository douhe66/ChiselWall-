"""索引管线（设计 §5）：连接器 -> 排除判定 -> 解析 -> 切块 -> FTS5 入库。

硬性红线：
    ① 只读用户文件（连接器只 open('rb')），绝不修改/移动/复制；
    ② chiselwall.db 在本机用户目录，绝不放挂载目录内（由 agent 层保证）；
    ④ 单文件解析失败 try/except 隔离并记入失败列表（docs.status='error'）；
    ⑤ 排除即不索引：status='excluded' 且无任何 chunk，检索与全文获取均不可得。

增量校验：以 (mtime, size) 快通道 + hash 兜底判断真正变更（设计 §5）：
    mtime/size 均未变 -> unchanged；变了才算 hash（大文件 >8MB 只采样头部 1MB，
    避免反复全量哈希拖慢扫描——资源预算内的折中，见 file_hash 注释）。
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from connectors.base import Connector, DocMeta, ParseError
from indexer.chunker import Chunker
from indexer.db import IndexDB
from indexer.jieba_util import tokenize as _default_tokenize

# 大文件哈希采样阈值与采样大小（资源保护）
HASH_SAMPLE_LIMIT = 8 * 1024 * 1024
HASH_SAMPLE_SIZE = 1024 * 1024


def file_hash(path: Path, size: int) -> str:
    """内容哈希：小文件全量 sha256；大文件（>8MB）采样头部 1MB。"""
    with open(path, "rb") as f:
        data = f.read() if size <= HASH_SAMPLE_LIMIT else f.read(HASH_SAMPLE_SIZE)
    return hashlib.sha256(data).hexdigest()


class Indexer:
    """全量扫描与单文件增量索引的统一入口。"""

    def __init__(
        self,
        db: IndexDB,
        chunker: Optional[Chunker] = None,
        tokenize_fn: Optional[Callable[[str], str]] = None,
        *,
        trigram_enabled: bool = False,          # F2：trigram 兜底默认关闭
        trigram_max_chars: int = 100_000,       # F2：仅小文档（总字符数 ≤ 阈值）写 trigram
        on_indexed: Optional[Callable[[int], None]] = None,  # M7：索引完成回调（向量化）
    ) -> None:
        self.db = db
        self.chunker = chunker or Chunker()
        self.tokenize = tokenize_fn or _default_tokenize
        self.trigram_enabled = trigram_enabled
        self.trigram_max_chars = trigram_max_chars
        self.on_indexed = on_indexed

    # ---- 全量 ----
    def scan_mount(
        self,
        mount_id: int,
        connector: Connector,
        progress: Optional[Callable[[str, str], None]] = None,
    ) -> dict:
        """启动全量校验 / 手动重建：遍历挂载，索引新增与变更，清理已删除。"""
        stats = {"indexed": 0, "unchanged": 0, "excluded": 0, "error": 0, "removed": 0}
        seen: set[str] = set()
        for meta in connector.list_docs():
            seen.add(meta.rel_path)
            result = self.index_meta(mount_id, connector, meta)
            stats[result] = stats.get(result, 0) + 1
            if progress:
                progress(meta.rel_path, result)
        # 对本次未枚举到的存量文档做 reconciliation：
        #   磁盘消失 -> 删除；仍存在 -> meta_of + index_meta
        #   （覆盖「目录被排除规则剪枝」：index_meta 会把文档翻转为 excluded 并摘除 chunk，
        #     红线⑤ 排除即不索引；也覆盖扩展名变化等边界）
        for rel in self.db.doc_paths(mount_id):
            if rel in seen:
                continue
            try:
                abs_path = connector.resolve(rel)
            except ParseError:
                continue
            if not abs_path.exists():
                self.db.delete_doc(mount_id, rel)
                stats["removed"] += 1
                continue
            try:
                meta = connector.meta_of(rel, abs_path)
            except ParseError:
                continue  # 非该连接器处理的文件（如思源挂载下的 assets），跳过
            result = self.index_meta(mount_id, connector, meta)
            stats[result] = stats.get(result, 0) + 1
        return stats

    # ---- 单文件（watchdog 事件与扫描共用） ----
    def index_file(self, mount_id: int, connector: Connector, rel_path: str) -> str:
        """单文件事件处理：不存在 -> 删除；目录 -> 重扫该目录；文件 -> 增量索引。

        返回：indexed / unchanged / excluded / error / removed / scanned:N / ignored
        """
        try:
            abs_path = connector.resolve(rel_path)
        except ParseError:
            return "ignored"
        if not abs_path.exists():
            self.db.delete_doc(mount_id, rel_path)
            return "removed"
        if abs_path.is_dir():
            return self.scan_dir(mount_id, connector, rel_path)
        try:
            meta = connector.meta_of(rel_path, abs_path)
        except ParseError:
            return "ignored"  # 例如思源挂载下的非 .sy 文件（assets 图片等）
        return self.index_meta(mount_id, connector, meta)

    def scan_dir(self, mount_id: int, connector: Connector, rel_prefix: str) -> str:
        """重扫某目录（目录新建/移入事件）：索引其中文档 + 存量文档 reconciliation。"""
        n = 0
        seen: set[str] = set()
        for meta in connector.list_docs(under=rel_prefix):
            seen.add(meta.rel_path)
            if self.index_meta(mount_id, connector, meta) == "indexed":
                n += 1
        prefix = rel_prefix.rstrip("/")
        for rel in self.db.doc_paths(mount_id):
            if rel == prefix or rel.startswith(prefix + "/"):
                if rel in seen:
                    continue
                try:
                    abs_path = connector.resolve(rel)
                except ParseError:
                    continue
                if not abs_path.exists():
                    self.db.delete_doc(mount_id, rel)
                    continue
                self.index_meta(mount_id, connector, connector.meta_of(rel, abs_path))
        return "scanned:" + str(n)

    def remove_dir(self, mount_id: int, rel_prefix: str) -> int:
        """目录删除/移走事件：摘除该目录下全部文档。返回删除数。"""
        return self.db.delete_docs_under(mount_id, rel_prefix)

    # ---- 核心 ----
    def index_meta(self, mount_id: int, connector: Connector, meta: DocMeta) -> str:
        """按签名判定并索引单个文档。

        返回：indexed / unchanged / excluded / error
        """
        if meta.excluded:
            # 红线⑤：排除即摘除 chunk，全文获取不可得
            doc_id = self.db.upsert_doc(
                mount_id,
                meta.rel_path,
                str(meta.abs_path),
                meta.title,
                meta.format,
                meta.mtime,
                meta.size,
                meta.hash,
                status="excluded",
                error=meta.exclude_reason,
            )
            self.db.clear_chunks(doc_id)
            return "excluded"
        try:
            st = meta.abs_path.stat()
        except OSError as exc:
            self._mark_error(mount_id, meta, "无法读取文件: " + str(exc))
            return "error"
        mtime, size = st.st_mtime, st.st_size
        prev = self.db.get_doc(mount_id, meta.rel_path)
        if prev and prev.status == "indexed" and prev.mtime == mtime and prev.size == size:
            return "unchanged"  # 快通道：mtime+size 未变（覆盖绝大多数事件）
        h = file_hash(meta.abs_path, size)  # 疑似变更才计算哈希（资源保护）
        if prev and prev.status == "indexed" and prev.hash == h and prev.size == size:
            return "unchanged"  # mtime 变了但内容未变（touch/同内容重写）：哈希兜底
        fresh = replace(meta, mtime=mtime, size=size, hash=h)
        doc_id = self.db.upsert_doc(
            mount_id,
            fresh.rel_path,
            str(fresh.abs_path),
            fresh.title,
            fresh.format,
            fresh.mtime,
            fresh.size,
            fresh.hash,
            status="pending",
        )
        try:
            parsed = connector.read_doc(fresh.rel_path)
        except ParseError as exc:
            self.db.set_doc_status(doc_id, "error", error=exc.message)
            return "error"
        except Exception as exc:  # 红线④：隔离任何意外异常，不阻断整体索引
            self.db.set_doc_status(doc_id, "error", error=exc.__class__.__name__ + ": " + str(exc))
            return "error"
        result = self.chunker.split(parsed.text)
        n = self.db.set_chunks(
            doc_id,
            result.chunks,
            self.tokenize,
            trigram=self.trigram_enabled,
            trigram_max_chars=self.trigram_max_chars,
        )
        self.db.set_doc_status(doc_id, "indexed", chunk_count=n, truncated=result.truncated, error=None)
        if self.on_indexed is not None:
            try:
                self.on_indexed(doc_id)
            except Exception:
                logging.getLogger("chiselwall.indexer").exception("索引后回调失败 doc_id=%s", doc_id)
        return "indexed"

    def _mark_error(self, mount_id: int, meta: DocMeta, message: str) -> None:
        self.db.upsert_doc(
            mount_id,
            meta.rel_path,
            str(meta.abs_path),
            meta.title,
            meta.format,
            meta.mtime,
            meta.size,
            meta.hash,
            status="error",
            error=message,
        )
