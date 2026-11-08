"""「直接打开」健壮性单测（M2-FIX3/FIX4/FIX6 用户验收反馈）。

FIX3 根因：无文件关联时 os.startfile 静默无效果。
FIX4 根因：注册表键存在但值为空串，旧实现误判为有关联。
FIX6 根因：系统「打开方式」选择框在用户桌面不可见（openas 抛 1155）——
    改为应用内方案：/open 返回 alternatives（注册表 ProgId 解析 VS Code + 记事本），
    UI 渲染「用 VS Code 打开 / 用记事本打开」按钮，不再依赖系统弹窗。

本文件用 mock（winreg / os.startfile / subprocess.Popen / Path.exists）单测，跨平台可跑。
运行：python -m unittest tests.test_open_file
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import node.agent as agent_mod


class _FakeKey:
    """伪造 winreg key 对象（with 语义 + QueryValueEx）。

    default/progid 为 None 表示「值不存在」（QueryValueEx 抛 OSError），
    "" 表示「值存在但为空串」（M2-FIX4 关键场景）。
    """

    def __init__(self, default=None, progid=None) -> None:
        self._default = default
        self._progid = progid

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def QueryValueEx(self, name: str):
        if name == "ProgId":
            if self._progid is None:
                raise OSError()
            return (self._progid, 1)
        if name == "":
            if self._default is None:
                raise OSError()
            return (self._default, 1)
        raise OSError()


class _FakeWinreg:
    """伪造 winreg 模块。

    ext_default / user_choice：None=键或值不存在；""=存在但空串。
    commands: key 路径（如 Applications\\Code.exe\\shell\\open\\command 的 progid 部分）
              -> 命令串（缺失视为无命令）。
    """

    HKEY_CLASSES_ROOT = 1
    HKEY_CURRENT_USER = 2

    def __init__(self, ext_default=None, user_choice=None, commands=None) -> None:
        self.ext_default = ext_default
        self.user_choice = user_choice
        self.commands = commands or {}
        self.opened: list = []

    def OpenKey(self, root, sub):
        self.opened.append((root, sub))
        s = str(sub)
        if "UserChoice" in s:
            if self.user_choice is None:
                raise OSError()
            return _FakeKey(progid=self.user_choice)
        if "shell\open\command" in s:
            progid = s.split("\shell\open\command")[0]
            cmd = self.commands.get(progid)
            if cmd is None:
                raise OSError()
            return _FakeKey(default=cmd)
        return _FakeKey(default=self.ext_default)

    def QueryValueEx(self, key, name):
        # 真 winreg 是模块级函数：转发到 key 对象
        return key.QueryValueEx(name)


# 可解析命令：含环境变量（静态不可校验，视为可用）
CMD_ENV = "%SystemRoot%\System32\notepad.exe %1"


class HasAssociationTest(unittest.TestCase):
    def test_user_choice_wins(self) -> None:
        progid = "Applications\notepad.exe"
        fake = _FakeWinreg(ext_default="", user_choice=progid, commands={progid: CMD_ENV})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertTrue(agent_mod._windows_has_association(Path("/f/note.md")))
        self.assertIn("UserChoice", str(fake.opened[0][1]))
        self.assertTrue(any("shell\open\command" in str(o[1]) for o in fake.opened))

    def test_hkcr_default_with_usable_command(self) -> None:
        fake = _FakeWinreg(
            ext_default="md_auto_file", user_choice=None, commands={"md_auto_file": CMD_ENV}
        )
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertTrue(agent_mod._windows_has_association(Path("/f/note.md")))

    def test_empty_values_no_association(self) -> None:
        # M2-FIX4 回归：键存在但值全为空串 -> 判为无关联
        fake = _FakeWinreg(ext_default="", user_choice="", commands={})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertFalse(agent_mod._windows_has_association(Path("/f/note.md")))

    def test_blank_progid_with_valid_hkcr(self) -> None:
        fake = _FakeWinreg(
            ext_default="md_auto_file", user_choice="", commands={"md_auto_file": CMD_ENV}
        )
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertTrue(agent_mod._windows_has_association(Path("/f/note.md")))

    def test_progid_without_command(self) -> None:
        fake = _FakeWinreg(ext_default="broken_progid", user_choice=None, commands={})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertFalse(agent_mod._windows_has_association(Path("/f/note.md")))

    def test_command_blank(self) -> None:
        fake = _FakeWinreg(ext_default="prog", user_choice=None, commands={"prog": ""})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertFalse(agent_mod._windows_has_association(Path("/f/note.md")))

    def test_no_extension(self) -> None:
        fake = _FakeWinreg(ext_default="md_auto_file", user_choice="", commands={})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertFalse(agent_mod._windows_has_association(Path("/f/README")))

    def test_winreg_missing_module(self) -> None:
        with mock.patch.dict(sys.modules, {"winreg": None}):
            self.assertFalse(agent_mod._windows_has_association(Path("/f/note.md")))


class WindowsOpenFileTest(unittest.TestCase):
    """_windows_open_file 返回 (ok, how, hint, alternatives)。"""

    def test_startfile_when_associated(self) -> None:
        p = Path("/f/note.md")
        with mock.patch.object(agent_mod, "_windows_has_association", return_value=True), \
             mock.patch.object(agent_mod.os, "startfile", create=True) as sf, \
             mock.patch.object(agent_mod, "_windows_alternatives", return_value=[]) as alts, \
             mock.patch.object(agent_mod.subprocess, "Popen") as popen:
            ok, how, hint, alternatives = agent_mod._windows_open_file(p)
            self.assertTrue(ok)
            self.assertEqual(how, "系统默认程序")
            self.assertEqual(hint, "")
            self.assertEqual(alternatives, [])
            sf.assert_called_once_with(str(p))
            alts.assert_not_called()
            popen.assert_not_called()

    def test_no_association_returns_alternatives_without_dialog(self) -> None:
        # M2-FIX6：无关联时不再弹系统对话框（用户桌面不可见），返回应用内方案
        p = Path("/f/note.md")
        alts_list = [{"app": "notepad", "label": "用记事本打开", "path": "notepad.exe"}]
        with mock.patch.object(agent_mod, "_windows_has_association", return_value=False), \
             mock.patch.object(agent_mod, "_windows_alternatives", return_value=alts_list), \
             mock.patch.object(agent_mod.subprocess, "Popen") as popen:
            ok, how, hint, alternatives = agent_mod._windows_open_file(p)
            self.assertTrue(ok)
            self.assertEqual(how, "无默认关联")
            self.assertIn("请选择打开方式", hint)
            self.assertEqual(alternatives, alts_list)
            popen.assert_not_called()  # 关键：不再 spawn rundll32

    def test_no_association_and_no_apps_hint(self) -> None:
        with mock.patch.object(agent_mod, "_windows_has_association", return_value=False), \
             mock.patch.object(agent_mod, "_windows_alternatives", return_value=[]):
            ok, how, hint, alternatives = agent_mod._windows_open_file(Path("/f/note.md"))
            self.assertIn("未找到可用应用", hint)
            self.assertEqual(alternatives, [])

    def test_startfile_raises_then_alternatives(self) -> None:
        alts_list = [{"app": "vscode", "label": "用 VS Code 打开", "path": "C:\\x\\Code.exe"}]
        with mock.patch.object(agent_mod, "_windows_has_association", return_value=True), \
             mock.patch.object(
                 agent_mod.os, "startfile", create=True, side_effect=OSError("no handler")
             ) as sf, \
             mock.patch.object(agent_mod, "_windows_alternatives", return_value=alts_list):
            ok, how, hint, alternatives = agent_mod._windows_open_file(Path("/f/note.md"))
            self.assertEqual(how, "无默认关联")
            self.assertEqual(alternatives, alts_list)
            sf.assert_called_once()


class AlternativesTest(unittest.TestCase):
    """VS Code ProgId 解析 / 记事本兜底（M2-FIX6）。"""

    def test_vscode_from_registry(self) -> None:
        fake = _FakeWinreg(
            commands={"Applications\\Code.exe": '"C:\\Program Files\\Microsoft VS Code\\Code.exe" "%1"'}
        )
        with mock.patch.dict(sys.modules, {"winreg": fake}), \
             mock.patch("node.agent.Path.exists", return_value=True):
            exe = agent_mod._find_vscode_exe()
        self.assertEqual(exe, "C:\\Program Files\\Microsoft VS Code\\Code.exe")

    def test_vscode_env_fallback(self) -> None:
        fake = _FakeWinreg(commands={})
        with mock.patch.dict(sys.modules, {"winreg": fake}), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\t"}), \
             mock.patch("node.agent.Path.exists", return_value=True):
            exe = agent_mod._find_vscode_exe()
        self.assertTrue(exe.endswith("Microsoft VS Code\\Code.exe"))

    def test_vscode_missing_exe_skipped(self) -> None:
        fake = _FakeWinreg(commands={"Applications\\Code.exe": '"C:\\x\\Code.exe" "%1"'})
        with mock.patch.dict(sys.modules, {"winreg": fake}), \
             mock.patch("node.agent.Path.exists", return_value=False):
            self.assertIsNone(agent_mod._find_vscode_exe())

    def test_notepad_always_in_alternatives(self) -> None:
        with mock.patch("node.agent._find_vscode_exe", return_value=None), \
             mock.patch("node.agent.Path.exists", return_value=True):
            alts = agent_mod._windows_alternatives()
        self.assertEqual([a["app"] for a in alts], ["notepad"])

    def test_vscode_and_notepad(self) -> None:
        with mock.patch("node.agent._find_vscode_exe", return_value="C:\\x\\Code.exe"), \
             mock.patch("node.agent.Path.exists", return_value=True):
            alts = agent_mod._windows_alternatives()
        self.assertEqual([a["app"] for a in alts], ["vscode", "notepad"])


if __name__ == "__main__":
    unittest.main()
