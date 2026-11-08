"""排除规则单测（设计 §7）：内置默认排除 + 用户规则（file/dir/glob）+ 红线⑤。

运行：python -m unittest tests.test_excludes 或 python tests/test_excludes.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.excludes import (
    DEFAULT_MAX_FILE_SIZE,
    ExcludeRule,
    ExclusionEngine,
    builtin_exclude_reason,
    looks_binary,
)


class BuiltinExcludeTest(unittest.TestCase):
    """内置默认排除（设计 §7）。"""

    def test_hidden(self) -> None:
        r = builtin_exclude_reason(Path("/a/.secret.md"), ".secret.md", is_dir=False, size=10)
        self.assertIsNotNone(r)
        self.assertIn("隐藏", r)

    def test_hidden_dir(self) -> None:
        r = builtin_exclude_reason(Path("/a/.config"), ".config", is_dir=True, size=None)
        self.assertIn("隐藏", r)

    def test_git_dir(self) -> None:
        # .git 同时命中隐藏目录规则与 BUILTIN_DIRS，隐藏规则在前——排除必须生效
        r = builtin_exclude_reason(Path("/a/.git"), ".git", is_dir=True, size=None)
        self.assertIsNotNone(r)
        self.assertIn(".git", r)

    def test_node_modules(self) -> None:
        r = builtin_exclude_reason(Path("/a/node_modules"), "node_modules", is_dir=True, size=None)
        self.assertIn("默认排除", r)

    def test_obsidian(self) -> None:
        r = builtin_exclude_reason(Path("/a/.obsidian"), ".obsidian", is_dir=True, size=None)
        self.assertIsNotNone(r)
        self.assertIn(".obsidian", r)

    def test_office_temp(self) -> None:
        r = builtin_exclude_reason(Path("/a/~$temp.md"), "~$temp.md", is_dir=False, size=10)
        self.assertIn("临时", r)

    def test_tmp_suffix(self) -> None:
        r = builtin_exclude_reason(Path("/a/x.tmp"), "x.tmp", is_dir=False, size=10)
        self.assertIn("临时", r)

    def test_binary_ext(self) -> None:
        r = builtin_exclude_reason(Path("/a/photo.jpg"), "photo.jpg", is_dir=False, size=100)
        self.assertIn("二进制", r)

    def test_legacy_doc(self) -> None:
        r = builtin_exclude_reason(Path("/a/老.doc"), "老.doc", is_dir=False, size=100)
        self.assertIn("旧格式", r)

    def test_empty_file(self) -> None:
        r = builtin_exclude_reason(Path("/a/empty.md"), "empty.md", is_dir=False, size=0)
        self.assertIn("空文件", r)

    def test_too_large(self) -> None:
        r = builtin_exclude_reason(Path("/a/big.md"), "big.md", is_dir=False, size=DEFAULT_MAX_FILE_SIZE + 1)
        self.assertIn("过大", r)

    def test_default_threshold_is_20mb(self) -> None:
        # 评审结论④：默认排除阈值 20MB（可配）
        self.assertEqual(DEFAULT_MAX_FILE_SIZE, 20 * 1024 * 1024)

    def test_supported_ok(self) -> None:
        self.assertIsNone(builtin_exclude_reason(Path("/a/note.md"), "note.md", is_dir=False, size=100))

    def test_plain_dir_ok(self) -> None:
        self.assertIsNone(builtin_exclude_reason(Path("/a/subdir"), "subdir", is_dir=True, size=None))


class UserRuleTest(unittest.TestCase):
    """用户规则：file / dir / glob（设计 §7）。"""

    def test_file_rule(self) -> None:
        eng = ExclusionEngine([ExcludeRule("file", "C:/notes/secret.md")])
        self.assertIsNotNone(eng.is_excluded(Path("C:/notes/secret.md"), "secret.md", is_dir=False, size=10))
        self.assertIsNone(eng.is_excluded(Path("C:/notes/other.md"), "other.md", is_dir=False, size=10))

    def test_dir_rule(self) -> None:
        eng = ExclusionEngine([ExcludeRule("dir", "C:/notes/private")])
        self.assertIsNotNone(eng.is_excluded(Path("C:/notes/private/a/b.md"), "a/b.md", is_dir=False, size=10))
        self.assertIsNotNone(eng.is_excluded(Path("C:/notes/private"), "private", is_dir=True, size=None))
        # 前缀相似但不同目录：不得误伤（private vs private2）
        self.assertIsNone(eng.is_excluded(Path("C:/notes/private2/x.md"), "private2/x.md", is_dir=False, size=10))

    def test_dir_rule_trailing_sep(self) -> None:
        eng = ExclusionEngine([ExcludeRule("dir", "C:/notes/private/")])
        self.assertIsNotNone(eng.is_excluded(Path("C:/notes/private/x.md"), "x.md", is_dir=False, size=10))

    def test_glob_rule(self) -> None:
        eng = ExclusionEngine([ExcludeRule("glob", "*.log")])
        self.assertIsNotNone(eng.is_excluded(Path("C:/n/a.log"), "a.log", is_dir=False, size=1))
        self.assertIsNone(eng.is_excluded(Path("C:/n/a.md"), "a.md", is_dir=False, size=1))

    def test_glob_subdir(self) -> None:
        eng = ExclusionEngine([ExcludeRule("glob", "draft/*")])
        self.assertIsNotNone(eng.is_excluded(Path("/n/draft/x.md"), "draft/x.md", is_dir=False, size=1))
        self.assertIsNone(eng.is_excluded(Path("/n/keep/x.md"), "keep/x.md", is_dir=False, size=1))

    def test_user_rule_priority_message(self) -> None:
        eng = ExclusionEngine([ExcludeRule("glob", "*.md")])
        r = eng.is_excluded(Path("/n/.h.md"), ".h.md", is_dir=False, size=1)
        self.assertIsNotNone(r)
        self.assertIn("用户排除", r)

    def test_glob_case_platform_behavior(self) -> None:
        # 评审观察-2：Windows 上 glob 大小写不敏感（fnmatch/normcase）；Linux 保持敏感
        eng = ExclusionEngine([ExcludeRule("glob", "*.LOG")])
        r = eng.is_excluded(Path("/n/a.log"), "a.log", is_dir=False, size=1)
        if os.name == "nt":
            self.assertIsNotNone(r)
        else:
            self.assertIsNone(r)


class BinarySniffTest(unittest.TestCase):
    def test_nul_bytes(self) -> None:
        self.assertTrue(looks_binary(b"abc\x00def"))

    def test_plain_text(self) -> None:
        self.assertFalse(looks_binary("你好世界".encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
