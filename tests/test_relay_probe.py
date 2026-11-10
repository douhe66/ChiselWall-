"""Relay 依赖错误必须可见；连通性握手测试。"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from node import bus


class RelayDependencyTest(unittest.TestCase):
    def test_missing_websockets_sets_error(self):
        async def noop(_msg):
            return None

        with mock.patch.object(bus, "websockets", None):
            client = bus.RelayClient(
                bus.RelayConfig(enabled=True, relay_url="ws://127.0.0.1:1/ws", nick="me"),
                noop,
            )
            client.start()
            self.assertIn("websockets", client.last_error)
            self.assertFalse(client.dependency_ok)

    def test_probe_missing_websockets(self):
        with mock.patch.object(bus, "websockets", None):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(bus.probe_relay("ws://127.0.0.1:1/ws", "me", "t", ["全员组"]))
            self.assertIn("websockets", str(ctx.exception))


class RelayProbeTest(unittest.TestCase):
    def test_probe_hello(self):
        try:
            import websockets
        except ImportError:
            self.skipTest("websockets not installed")

        async def handler(ws):
            raw = await ws.recv()
            msg = json.loads(raw)
            await ws.send(json.dumps({
                "type": "hello_ok",
                "user": msg.get("user"),
                "online": [{"user": msg.get("user")}],
                "groups": [{"name": "全员组", "online": 1}],
            }, ensure_ascii=False))

        async def run():
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                return await bus.probe_relay("ws://127.0.0.1:%d/ws" % port, "me", "t", ["全员组"])

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertEqual(result["nick"], "me")
        self.assertEqual(result["online"], 1)


if __name__ == "__main__":
    unittest.main()
