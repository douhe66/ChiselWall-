"""Connector 协议（设计文档 §6 统一接口）。

统一接口：
    list_docs()          -> Iterator[DocMeta]  列出候选文档（含被排除项与原因）
    read_doc(rel_path)   -> ParsedDoc          解析出纯文本（只读，绝不写回）
    formats()            -> frozenset[str]     支持的扩展名
    default_excludes()   -> list[str]          连接器默认排除 glob
    resolve(rel_path)    -> Path               相对路径 -> 绝对路径（防路径穿越）

M2 实现：DirectoryConnector / ObsidianConnector（txt/md/docx/xlsx/pdf/csv）。
预留（§16，只登记不动工）：SiyuanConnector（M3）、Notion/Youdao/LinkConnector（M6 后）、
.pptx 解析（python-pptx 可选）、.doc/.xls 旧格式转换（明确不做）。

硬性红线①：连接器只读，任何实现不得修改/移动/复制用户笔记文件。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

# 支持的文本格式（扩展名小写、不含点）。
# json：仅限元宝对话导出结构（M2.5，connectors/yuanbao.py 结构识别，非元宝 .json 走排除轨道）
SUPPORTED_EXTS: frozenset[str] = frozenset({"txt", "md", "csv", "docx", "xlsx", "pdf", "json"})

# 旧二进制格式：跳过并提示（设计 §6；转换明确不承诺，§16）
LEGACY_EXTS: frozenset[str] = frozenset({"doc", "xls", "ppt"})

# 明显二进制/媒体/压缩/字体/数据库扩展名：默认排除（设计 §7）
BINARY_EXTS: frozenset[str] = frozenset({
    "exe", "dll", "so", "dylib", "bin", "class", "jar", "pyc", "o", "a", "lib", "msi",
    "jpg", "jpeg", "png", "gif", "bmp", "ico", "webp", "tif", "tiff", "heic", "psd", "ai",
    "mp3", "mp4", "avi", "mkv", "mov", "wmv", "flv", "flac", "wav", "aac", "wma", "m4a",
    "zip", "7z", "rar", "gz", "tgz", "bz2", "xz", "tar", "iso", "img", "cab",
    "ttf", "otf", "woff", "woff2", "eot",
    "db", "sqlite", "sqlite3", "db3", "mdb", "accdb", "pst", "ost",
    "lnk", "sys", "dmp", "core",
})


class ParseError(Exception):
    """单文件解析失败（内容损坏、加密、旧格式、无文本层、被排除等）。

    由 indexer 隔离捕获并记入失败列表（红线④：单文件失败不阻断整体索引）。
    """

    def __init__(self, message: str, *, code: str = "parse_error") -> None:
        super().__init__(message)
        self.message: str = message
        self.code: str = code


@dataclass(frozen=True)
class DocMeta:
    """一个候选文档的元信息（相对挂载根的路径；不读正文）。

    excluded=True 表示命中排除规则（exclude_reason 说明原因），
    该文档不参与解析与索引（红线⑤：排除即完全不可见）。
    """

    rel_path: str                      # 相对挂载根；单文件挂载时为文件名
    abs_path: Path
    title: str                         # 展示标题（默认取文件名主干）
    format: str                        # 扩展名小写（不含点）
    mtime: float                       # stat.st_mtime（增量校验用）
    size: int                          # 字节数（增量校验用）
    hash: Optional[str] = None         # 内容哈希（indexer 在变更时计算，详见 indexer 注释）
    excluded: bool = False
    exclude_reason: Optional[str] = None

    @property
    def ext(self) -> str:
        return self.format


@dataclass
class ParsedDoc:
    """解析结果：纯文本 + 是否触及单文档文本上限（设计 §5：默认 5MB）。"""

    meta: DocMeta
    text: str
    truncated: bool = False


class Connector(ABC):
    """连接器协议基类。所有实现必须只读。"""

    kind: str = "directory"  # directory / obsidian / file（挂载类型）

    def __init__(self, root: Path, *, text_cap_chars: int = 5_000_000) -> None:
        self.root: Path = root.resolve()
        # 单文件文本上限（设计 §5 超大文件截断；默认 500 万字符 ≈ 5MB 纯文本）
        self.text_cap_chars: int = text_cap_chars

    # ---- 协议方法 ----
    @abstractmethod
    def list_docs(self) -> Iterator[DocMeta]:
        """列出候选文档（含被排除项）。"""
        raise NotImplementedError

    @abstractmethod
    def read_doc(self, rel_path: str) -> ParsedDoc:
        """解析单个文档为纯文本；失败抛 ParseError。只读，绝不写回。"""
        raise NotImplementedError

    def formats(self) -> frozenset[str]:
        """支持的扩展名。"""
        return SUPPORTED_EXTS

    def default_excludes(self) -> list[str]:
        """连接器默认排除 glob（设计 §6）。内置规则在 ExclusionEngine。"""
        return []

    # ---- 监视范围（watchdog 用） ----
    def watch_root(self) -> Optional[Path]:
        """需要监视的根目录；单文件挂载监视父目录。None 表示不监视。"""
        return self.root if self.root.is_dir() else self.root.parent

    def watch_allowed(self, path: Path) -> bool:
        """该目录是否允许注册 watch（默认只允许挂载根及其子目录）。"""
        try:
            p = path.resolve()
        except OSError:
            return False
        if self.root.is_file():
            return p == self.root.parent
        return p == self.root or self.root in p.parents

    # ---- 公共工具 ----
    def resolve(self, rel_path: str) -> Path:
        """rel_path -> 绝对路径；必须位于挂载根之内（防路径穿越）。

        单文件挂载时根即文件本身，rel_path 必须恰好指回该文件。
        """
        base = self.root.parent if self.root.is_file() else self.root
        abs_path = (base / rel_path).resolve()
        if self.root.is_file():
            if abs_path != self.root:
                raise ParseError(f"路径越界: {rel_path}", code="path_traversal")
        elif abs_path != self.root and self.root not in abs_path.parents:
            raise ParseError(f"路径越界: {rel_path}", code="path_traversal")
        return abs_path

    @staticmethod
    def norm_rel(rel_path: str) -> str:
        """统一相对路径分隔符（跨平台：入库一律用 '/'）。"""
        return rel_path.replace(os.sep, "/").replace("\\", "/")

    def _cap_text(self, text: str) -> tuple[str, bool]:
        """应用单文档文本上限，返回 (截断后文本, 是否截断)。"""
        if len(text) <= self.text_cap_chars:
            return text, False
        return text[: self.text_cap_chars], True
