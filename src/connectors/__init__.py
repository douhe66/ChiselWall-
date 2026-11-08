"""连接器包：把各种笔记来源抽象为统一的 Connector 协议（设计 §6）。

M2 实现：DirectoryConnector / ObsidianConnector（txt/md/docx/xlsx/pdf/csv/json 元宝对话）。
M3 实现：SiyuanConnector（.sy=Lute AST JSON 直读，Spec 2-6 兼容）。
预留（§16，只登记不动工）：
    - 思源内核 API 增强（127.0.0.1:6806 + Token）、官方 CLI siyuan export md 兜底通道
    - Notion / Youdao / LinkConnector——M6 后按优先级
    - .doc/.xls/.ppt 旧格式解析——明确不承诺
"""
from .base import (
    BINARY_EXTS,
    LEGACY_EXTS,
    SUPPORTED_EXTS,
    Connector,
    DocMeta,
    ParsedDoc,
    ParseError,
)
from .directory import DirectoryConnector, decode_text, strip_frontmatter
from .excludes import (
    BUILTIN_DIRS,
    DEFAULT_MAX_FILE_SIZE,
    ExcludeRule,
    ExclusionEngine,
    builtin_exclude_reason,
    looks_binary,
)
from .obsidian import ObsidianConnector
from .siyuan import (
    EXCLUDED_WORKSPACE_DIRS,
    SiyuanConnector,
    node_text,
    parse_sy_file,
    sy_doc_text,
    sy_doc_title,
)

__all__ = [
    "BINARY_EXTS",
    "LEGACY_EXTS",
    "SUPPORTED_EXTS",
    "Connector",
    "DocMeta",
    "ParsedDoc",
    "ParseError",
    "DirectoryConnector",
    "ObsidianConnector",
    "SiyuanConnector",
    "EXCLUDED_WORKSPACE_DIRS",
    "node_text",
    "parse_sy_file",
    "sy_doc_text",
    "sy_doc_title",
    "decode_text",
    "strip_frontmatter",
    "BUILTIN_DIRS",
    "DEFAULT_MAX_FILE_SIZE",
    "ExcludeRule",
    "ExclusionEngine",
    "builtin_exclude_reason",
    "looks_binary",
]
