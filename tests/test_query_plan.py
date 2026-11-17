"""发送前 QueryPlan：解析、软 AND 覆盖、无 AI 回退。"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.query_plan import evaluate_plan
from ai.query_rewrite import QueryExpandConfig, QueryPlan, QueryRewriter, parse_plan
from node.bus import MAX_ANSWER_BYTES, NodeBus, RelayConfig, fit_answer_payload
from searcher.search import SearchHit


def hit(doc_id: int, score: float = 1.0) -> SearchHit:
    return SearchHit(
        doc_id=doc_id, mount_id=1, title="doc%d" % doc_id, rel_path="%d.md" % doc_id,
        abs_path="", format="md", score=score, chunk_seq=0,
        snippet_html="", snippet_text="", route="jieba",
    )


class PlanTest(unittest.TestCase):
    def test_parse_and_expression(self):
        raw = '{"clauses":[{"terms":["文件锁","flock"]},{"terms":["Linux"]}],"paraphrases":["Linux 文件锁用法"]}'
        plan = parse_plan(raw, "Linux 文件锁怎么用")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.clauses, [["文件锁", "flock"], ["Linux"]])
        self.assertIn("(文件锁 OR flock) AND (Linux)", plan.expression)

    def test_generic_terms_filtered(self):
        raw = '{"clauses":[{"terms":["Linux","linux"]},{"terms":["文件锁","flock"]},{"terms":["使用","用法","示例"]}]}'
        plan = parse_plan(raw, "Linux 文件锁怎么用")
        self.assertEqual(plan.clauses, [["Linux"], ["文件锁", "flock"]])

    def test_no_ai_returns_none(self):
        from unittest import mock

        ai = mock.Mock()
        ai.ready = False
        self.assertIsNone(QueryRewriter(QueryExpandConfig(enabled=True), ai).plan("问题"))

    def test_bad_json(self):
        self.assertIsNone(parse_plan("not json", "q"))
        self.assertIsNone(parse_plan('{"clauses":[]}', "q"))

    def test_soft_and_keeps_candidates(self):
        class FakeSearcher:
            def search(self, text, _topk=8):
                if text == "Linux 文件锁":
                    return [hit(1, 1.0), hit(2, 0.9)]
                if text == "文件锁":
                    return [hit(1, 0.8), hit(3, 0.7)]
                if text == "flock":
                    return [hit(1, 0.6)]
                if text == "Linux":
                    return [hit(1, 0.5)]
                if text == "Linux 文件锁用法":
                    return [hit(4, 0.4)]
                return []

        plan = QueryPlan("Linux 文件锁", [["文件锁", "flock"], ["Linux"]], ["Linux 文件锁用法"])
        hits = evaluate_plan(FakeSearcher(), plan, topk=5)
        self.assertEqual(hits[0].doc_id, 1)
        self.assertEqual(hits[0].plan_coverage, 2)
        self.assertIn(2, [h.doc_id for h in hits])
        self.assertIn(3, [h.doc_id for h in hits])
        self.assertIn(4, [h.doc_id for h in hits])
        self.assertEqual(hits[0].route, "plan")


class BusPlanTest(unittest.TestCase):
    def _bus(self, plan_fn, plan_ready_fn=None):
        class FakeSearcher:
            def search(self, text, _topk=8):
                return [hit(1)] if text else []

        bus = NodeBus(
            RelayConfig(enabled=True, relay_url="ws://x", nick="alice"),
            FakeSearcher(), plan_fn=plan_fn, local_searcher=FakeSearcher(),
            plan_ready_fn=plan_ready_fn,
        )
        bus.client.connected = True
        sent = []

        async def fake_send(payload):
            sent.append(payload)
            return True

        bus.client.send = fake_send
        return bus, sent

    def test_plan_is_sent(self):
        plan = QueryPlan("Linux 文件锁", [["文件锁"], ["Linux"]], [])
        bus, sent = self._bus(lambda q: plan)
        asyncio.run(bus.ask("Linux 文件锁", ["全员组"]))
        self.assertEqual(sent[0]["text"], "Linux 文件锁")
        self.assertEqual(sent[0]["plan"]["expression"], "(文件锁) AND (Linux)")

    def test_research_search_bypasses_second_plan(self):
        plan = QueryPlan("原始问题", [["扩展"]], [])
        bus, sent = self._bus(lambda q: plan)
        asyncio.run(bus.ask("原始问题", ["全员组"], use_plan=False))
        self.assertEqual(sent[0]["text"], "原始问题")
        self.assertIsNone(sent[0]["plan"])

    def test_none_falls_back_original(self):
        bus, sent = self._bus(lambda q: None)
        asyncio.run(bus.ask("原始问题", ["全员组"]))
        self.assertEqual(sent[0]["text"], "原始问题")
        self.assertIsNone(sent[0]["plan"])

    def test_not_ready_skips_plan_without_wait(self):
        calls = []

        def plan_fn(q):
            calls.append(q)
            raise AssertionError("AI 未就绪时不应调用 QueryPlan")

        bus, sent = self._bus(plan_fn, plan_ready_fn=lambda: False)
        asyncio.run(bus.ask("原始问题", ["全员组"]))
        self.assertEqual(calls, [])
        self.assertEqual(sent[0]["text"], "原始问题")
        self.assertIsNone(sent[0]["plan"])

    def test_answer_payload_fits_relay_limit(self):
        hits = [
            {"doc_id": i, "title": "文档%d" % i, "rel_path": "%d.md" % i,
             "snippet_text": "片段", "ai_text": "中" * 12000}
            for i in range(8)
        ]
        payload = {"type": "answer", "qid": "q1", "node": "我", "hits": hits}
        fitted, removed, dropped = fit_answer_payload(payload)
        wire = len(__import__("json").dumps(fitted, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(wire, MAX_ANSWER_BYTES)
        self.assertEqual(len(fitted["hits"]), 8)
        self.assertGreater(removed, 0)
        self.assertEqual(dropped, 0)
        self.assertTrue(fitted["context_truncated"])

    def test_small_answer_payload_untouched(self):
        payload = {"type": "answer", "qid": "q1", "node": "我", "hits": [{"title": "t", "ai_text": "ok"}]}
        fitted, removed, dropped = fit_answer_payload(payload)
        self.assertEqual((removed, dropped), (0, 0))
        self.assertNotIn("context_truncated", fitted)

    def test_not_ready_skips_legacy_rewrite(self):
        probe_calls = []
        rewrite_calls = []
        bus, sent = self._bus(lambda q: None, plan_ready_fn=lambda: False)
        bus.searcher.search = lambda text, topk=5: probe_calls.append(text) or []
        bus.rewrite_fn = lambda text: rewrite_calls.append(text) or ["改写词"]
        asyncio.run(bus.ask("原始问题", ["全员组"]))
        self.assertEqual(probe_calls, [])
        self.assertEqual(rewrite_calls, [])
        self.assertEqual(sent[0]["text"], "原始问题")


if __name__ == "__main__":
    unittest.main()
