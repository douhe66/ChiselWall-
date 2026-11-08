"""挂载类型自动识别单测（M3 用户反馈：误挂思源笔记本目录导致 .sy 不被识别）。

运行：python -m unittest tests.test_mount_detect
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from node.agent import Config, NodeAgent


def _sy(doc_id: str, title: str) -> str:
    return json.dumps(
        {
            "ID": doc_id,
            "Spec": "2",
            "Type": "NodeDocument",
            "Properties": {"id": doc_id, "title": title, "type": "doc", "updated": "20250101120000"},
            "Children": [
                {
                    "Type": "NodeParagraph",
                    "ID": doc_id + "-p1",
                    "Properties": {"id": doc_id + "-p1", "updated": "20250101120000"},
                    "Children": [{"Type": "NodeText", "Data": "自动识别测试正文"}],
                }
            ],
        },
        ensure_ascii=False,
    )


class MountDetectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.agent = NodeAgent(Config(db_path=self.base / "c.db", config_dir=self.base / "cfg"))
        # 伪造一个思源工作区：conf/conf.json + data/<notebook>/<doc>.sy
        self.ws = self.base / "siyuan-ws"
        (self.ws / "conf").mkdir(parents=True)
        (self.ws / "conf" / "conf.json").write_text("{}", encoding="utf-8")
        self.notebook = self.ws / "data" / "20250101120000-notebook"
        self.notebook.mkdir(parents=True)
        (self.notebook / "20250101120000-doc1.sy").write_text(_sy("20250101120000-doc1", "工作区文档"), encoding="utf-8")

    def tearDown(self) -> None:
        self.agent.stop()
        self.tmp.cleanup()

    def test_workspace_root_auto_siyuan(self) -> None:
        r = self.agent.add_mount(str(self.ws))
        self.assertEqual(r["kind"], "siyuan")
        from connectors.siyuan import SiyuanConnector

        self.assertIsInstance(self.agent.connector_of(r["mount_id"]), SiyuanConnector)

    def test_data_subdir_rejected_with_guidance(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.agent.add_mount(str(self.notebook))
        msg = str(ctx.exception)
        self.assertIn("工作区根目录", msg)
        self.assertIn(str(self.ws), msg)

    def test_obsidian_auto(self) -> None:
        vault = self.base / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        (vault / "a.md").write_text("笔记", encoding="utf-8")
        r = self.agent.add_mount(str(vault))
        self.assertEqual(r["kind"], "obsidian")

    def test_plain_dir_auto(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        (plain / "a.md").write_text("笔记", encoding="utf-8")
        r = self.agent.add_mount(str(plain))
        self.assertEqual(r["kind"], "directory")

    def test_explicit_directory_bypasses_guidance(self) -> None:
        r = self.agent.add_mount(str(self.notebook), kind="directory")
        self.assertEqual(r["kind"], "directory")

    def test_file_auto(self) -> None:
        f = self.base / "plain" / "a.md"
        (self.base / "plain").mkdir()
        f.write_text("笔记", encoding="utf-8")
        r = self.agent.add_mount(str(f))
        self.assertEqual(r["kind"], "file")


if __name__ == "__main__":
    unittest.main()