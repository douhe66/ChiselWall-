"""AI evidence fetch：按排名取远端全文，直到 max_context_chars。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from node.agent import Config, NodeAgent


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = NodeAgent(Config(
            db_path=Path(self.tmp.name) / "c.db",
            config_dir=Path(self.tmp.name) / "cfg",
        ))
        self.session = self.agent.ask_session_create("test")
        self.sid = self.session["id"]
        self.agent.ask_sessions.append(
            self.sid, "user", "香港 IP 是什么",
            {"aggregated": [{
                "title": "香港", "rel_path": "香港.md", "format": "md",
                "snippet_text": "IP 段：10.0.0.0/8", "tier": "strong",
                "nodes": ["peer"],
                "sources": [{"node": "peer", "doc_id": 7, "rel_path": "香港.md"}],
            }]},
        )

    def tearDown(self):
        self.agent.ask_sessions.close()
        self.agent.ai_sessions.close()
        self.agent.db.close()
        self.tmp.cleanup()

    def test_fetch_full_text_until_budget(self):
        calls = []

        async def fake_fetch(node, doc_id, timeout_s=15.0, max_chars=8000):
            calls.append((node, doc_id, max_chars))
            return "香港的 IP 段是 10.0.0.0/8\n" + ("详情" * 3000)

        self.agent.bus.fetch = fake_fetch
        result = asyncio.run(self.agent.resolve_ai_evidence(self.sid))
        self.assertEqual(result["fetched"], 1)
        self.assertTrue(calls)
        turns = self.agent.ask_sessions.turns(self.sid)
        payload = turns[-1]["payload"]
        hit = payload["aggregated"][0]
        self.assertTrue(hit.get("evidence_full"))
        self.assertIn("香港的 IP 段", hit["ai_text"])
        self.assertGreater(len(hit["ai_text"]), 2000)

    def test_fetch_failure_keeps_snippet(self):
        async def fake_fetch(node, doc_id, timeout_s=15.0, max_chars=8000):
            raise ValueError("offline")

        self.agent.bus.fetch = fake_fetch
        result = asyncio.run(self.agent.resolve_ai_evidence(self.sid))
        self.assertEqual(result["failed"], 1)
        turns = self.agent.ask_sessions.turns(self.sid)
        hit = turns[-1]["payload"]["aggregated"][0]
        self.assertFalse(hit.get("evidence_full"))
        self.assertEqual(hit["snippet_text"], "IP 段：10.0.0.0/8")


if __name__ == "__main__":
    unittest.main()
