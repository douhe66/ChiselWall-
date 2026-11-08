"""M6 relay 配置持久化（设置页）与组管理 API 单测。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from api.server import create_app
from node.agent import Config, NodeAgent


class RelayConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cfg_dir = base / "cfg"
        self.agent = NodeAgent(Config(db_path=base / "c.db", config_dir=self.cfg_dir))
        self.client = TestClient(create_app(self.agent, token="t"))

    def tearDown(self) -> None:
        self.agent.stop()
        self.tmp.cleanup()

    def test_save_persists_and_reloads(self) -> None:
        r = self.client.post(
            "/api/relay/config",
            json={"relay_url": "ws://127.0.0.1:18760/ws", "nick": "张三", "token": "tok-1234",
                  "groups": ["全员组", "数据库组"]},
            headers={"X-Chisel-Token": "t"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["nick"], "张三")
        self.assertEqual(data["groups"], ["全员组", "数据库组"])
        self.assertTrue(data["has_token"])
        # 落盘 + 新实例读取
        self.assertTrue((self.cfg_dir / "relay.json").exists())
        agent2 = NodeAgent(Config(db_path=Path(self.tmp.name) / "d.db", config_dir=self.cfg_dir))
        try:
            pub = agent2.relay_public()
            self.assertEqual(pub["relay_url"], "ws://127.0.0.1:18760/ws")
            self.assertEqual(pub["groups"], ["全员组", "数据库组"])
        finally:
            agent2.stop()

    def test_empty_url_disables(self) -> None:
        r = self.client.post(
            "/api/relay/config",
            json={"relay_url": "", "nick": "张三"},
            headers={"X-Chisel-Token": "t"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["enabled"])

    def test_group_create_requires_connection(self) -> None:
        r = self.client.post(
            "/api/relay/groups",
            json={"name": "平台组", "description": "平台相关"},
            headers={"X-Chisel-Token": "t"},
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
