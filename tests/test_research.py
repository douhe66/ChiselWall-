"""EvidenceSession 与 ResearchAgent 工具循环。"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.llm import AIConfig
from retrieval.research import ResearchAgent, _parse_action
from retrieval.session import EvidenceSession


def hit(doc_id=1, title="香港", snippet="IP 段：10.0.0.0/8"):
    return {
        "doc_id": doc_id, "title": title, "rel_path": title + ".md", "format": "md",
        "tier": "strong", "score": 1.0, "snippet_text": snippet,
        "match_fields": ["title", "content"],
        "sources": [{"node": "alice", "doc_id": doc_id}],
    }


class FakeKernel:
    def __init__(self):
        self.fetches = []
        self.searches = []

    async def fetch(self, session, evidence_id, max_chars=24000):
        self.fetches.append((evidence_id, max_chars))
        ev = session.get(evidence_id)
        ev.fetched = True
        ev.full_text = "完整正文：香港的 IP 段是 10.0.0.0/8"
        return ev

    async def search(self, session, query, topk=8):
        self.searches.append(query)
        return session.add_hits([hit(2, "香港网络", "更多 IP 段")])


class ResearchTest(unittest.TestCase):
    def test_session_cards_and_dedup(self):
        s = EvidenceSession("香港 IP", ["全员组"])
        s.add_hits([hit(1), hit(1), hit(2)])
        self.assertEqual(len(s.evidence), 2)
        self.assertEqual(s.cards()[0]["evidence_id"], "e1")

    def test_parse_action(self):
        self.assertEqual(_parse_action('{"action":"answer","text":"x"}')["action"], "answer")
        self.assertEqual(_parse_action('说明\n{"action":"search","query":"x"}\n')["action"], "search")

    def test_fetch_then_answer(self):
        session = EvidenceSession("香港 IP", ["全员组"])
        session.add_hits([hit(1)])
        cfg = AIConfig(enabled=True, base_url="http://x", model="m")
        kernel = FakeKernel()
        agent = ResearchAgent(kernel, cfg)
        replies = [
            {"text": '{"action":"fetch","evidence_ids":["e1"],"reason":"片段不足"}'},
            {"text": '{"action":"answer","text":"香港是 10.0.0.0/8","citations":["e1"]}'},
        ]
        with mock.patch("retrieval.research.LLMClient.chat", side_effect=replies):
            result = asyncio.run(agent.run(session))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["citations"], ["e1"])
        self.assertEqual(kernel.fetches[0][0], "e1")
        self.assertTrue(session.get("e1").fetched)

    def test_empty_session_searches_first(self):
        session = EvidenceSession("香港 IP", ["全员组"])
        cfg = AIConfig(enabled=True, base_url="http://x", model="m")
        kernel = FakeKernel()
        agent = ResearchAgent(kernel, cfg)
        replies = [
            {"text": '{"action":"search","query":"香港 IP"}'},
            {"text": '{"action":"answer","text":"见资料","citations":["e1"]}'},
        ]
        with mock.patch("retrieval.research.LLMClient.chat", side_effect=replies):
            result = asyncio.run(agent.run(session))
        self.assertEqual(result["status"], "ready")
        self.assertIn("香港 IP", kernel.searches)

    def test_search_then_answer(self):
        session = EvidenceSession("香港 IP", ["全员组"])
        session.add_hits([hit(1)])
        cfg = AIConfig(enabled=True, base_url="http://x", model="m")
        kernel = FakeKernel()
        agent = ResearchAgent(kernel, cfg)
        replies = [
            {"text": '{"action":"search","query":"香港 CIDR"}'},
            {"text": '{"action":"answer","text":"见资料","citations":["e2"]}'},
        ]
        with mock.patch("retrieval.research.LLMClient.chat", side_effect=replies):
            result = asyncio.run(agent.run(session))
        self.assertEqual(result["status"], "ready")
        self.assertIn("香港 CIDR", kernel.searches)
        self.assertEqual(result["citations"], ["e2"])

    def test_unconfigured(self):
        session = EvidenceSession("q")
        result = asyncio.run(ResearchAgent(FakeKernel(), AIConfig()).run(session))
        self.assertEqual(result["status"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
