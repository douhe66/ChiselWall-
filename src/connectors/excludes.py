"""排除规则与隐私（设计 §7；红线⑤）。

排除判定在扫描（list_docs）、增量事件（watcher）、全文获取（API/docs）三处统一生效：
排除即不索引、不参与应答、全文获取也不可得。

默认排除（内置，无需配置）：
    - 隐藏文件/目录（'.' 开头；跨平台按名字判断，不依赖 Windows 隐藏属性位）
    - .git / .svn / node_modules / .obsidian
    - Office 临时文件（~$ 前缀）与 .tmp/.swp/.temp 等临时文件
    - 空文件（0 字节）
    - 二进制/媒体/压缩/字体/数据库扩展名（见 base.BINARY_EXTS）
    - 旧格式 .doc/.xls/.ppt（跳过并提示，§16 明确不做转换）
    - 超过阈值的大文件（默认 20MB，可配；评审结论④）
用户规则（可在 UI 增删，持久化在 exclude_rules 表）：
    - file：排除单个文件（绝对路径精确匹配）
    - dir ：排除整目录（绝对路径前缀匹配，按路径段）
    - glob：通配符（fnmatch；Windows 大小写不敏感、Linux 保持敏感，评审观察-2；
      对绝对路径/相对路径/文件名三处匹配）
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .base import BINARY_EXTS, LEGACY_EXTS, SUPPORTED_EXTS

# 默认排除的目录名（名字匹配，跨平台）
BUILTIN_DIRS: frozenset[str] = frozenset({".git", ".svn", "node_modules", ".obsidian"})
# Office 临时文件前缀 / 常见临时文件后缀
TEMP_PREFIXES: tuple[str, ...] = ("~$",)
TEMP_SUFFIXES: tuple[str, ...] = (".tmp", ".swp", ".temp")
# 大文件阈值（评审结论④：默认排除 20MB、可配）
DEFAULT_MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20MB


def _norm_case(p: str) -> str:
    """Windows 路径大小写不敏感；Linux 敏感。"""
    return os.path.normcase(p) if os.name == "nt" else p


@dataclass(frozen=True)
class ExcludeRule:
    """一条用户排除规则。"""

    kind: str  # 'file' | 'dir' | 'glob'
    pattern: str

    def matches(self, abs_path: Path, rel_path: str) -> bool:
        a = str(abs_path)
        if self.kind == "file":
            return _norm_case(a) == _norm_case(self.pattern)
        if self.kind == "dir":
            p = self.pattern.rstrip("\\/")
            return (
                _norm_case(a) == _norm_case(p)
                or _norm_case(a).startswith(_norm_case(p) + "/")
                or _norm_case(a).startswith(_norm_case(p) + "\\")
            )
        # glob：三处匹配（绝对路径 / 相对路径 / 文件名）。
        # 大小写（评审观察-2）：Windows 用 fnmatch（内部 normcase，不敏感）；Linux 用 fnmatchcase（保持敏感）
        if os.name == "nt":
            return (
                fnmatch.fnmatch(a, self.pattern)
                or fnmatch.fnmatch(rel_path, self.pattern)
                or fnmatch.fnmatch(abs_path.name, self.pattern)
            )
        return (
            fnmatch.fnmatchcase(a, self.pattern)
            or fnmatch.fnmatchcase(rel_path, self.pattern)
            or fnmatch.fnmatchcase(abs_path.name, self.pattern)
        )


def builtin_exclude_reason(
    abs_path: Path,
    rel_path: str,
    *,
    is_dir: bool,
    size: Optional[int] = None,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
) -> Optional[str]:
    """内置默认排除判定：返回排除原因字符串；不排除返回 None。"""
    name = abs_path.name
    if name.startswith(".") and name not in {".", ".."}:
        return f"隐藏文件/目录: {name}"
    if name in BUILTIN_DIRS:
        return f"默认排除目录: {name}"
    if is_dir:
        return None
    if name.startswith(TEMP_PREFIXES):
        return f"Office 临时文件: {name}"
    if name.endswith(TEMP_SUFFIXES):
        return f"临时文件: {name}"
    if size == 0:
        return "空文件"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in LEGACY_EXTS:
        return f"旧格式 .{ext} 暂不支持（转换明确不承诺，§16）"
    if ext in BINARY_EXTS:
        return f"二进制/媒体文件: .{ext}"
    if size is not None and size > max_file_size:
        return f"文件过大（>{max_file_size // (1024 * 1024)}MB）"
    return None


def looks_binary(head: bytes) -> bool:
    """内容探测二进制：前 1024 字节含 NUL 即视为二进制（防伪装扩展名）。"""
    return b"\x00" in head[:1024]


class ExclusionEngine:
    """内置规则 + 用户规则的统一判定入口。"""

    def __init__(
        self,
        rules: Optional[Iterable[ExcludeRule]] = None,
        *,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        self.rules: list[ExcludeRule] = list(rules or [])
        self.max_file_size: int = max_file_size

    def set_rules(self, rules: Iterable[ExcludeRule]) -> None:
        self.rules = list(rules)

    def user_rule_reason(self, abs_path: Path, rel_path: str) -> Optional[str]:
        for r in self.rules:
            if r.matches(abs_path, rel_path):
                return f"用户排除规则({r.kind}): {r.pattern}"
        return None

    def is_excluded(
        self,
        abs_path: Path,
        rel_path: str,
        *,
        is_dir: bool,
        size: Optional[int] = None,
    ) -> Optional[str]:
        """返回排除原因；None 表示可索引。用户规则优先于内置规则（便于 UI 溯源）。"""
        reason = self.user_rule_reason(abs_path, rel_path)
        if reason:
            return reason
        return builtin_exclude_reason(
            abs_path, rel_path, is_dir=is_dir, size=size, max_file_size=self.max_file_size
        )
