"""P2 查询改写单测：解析、配置、RRF 合并、缓存。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.query_rewrite import (
    QueryExpandConfig,
    QueryExpandStore,
    QueryRewriter,
    parse_rewrites,
)
from node.agent import Config, NodeAgent
from searcher.search import SearchHit


def make_hit(doc_id: int, seq: int = 0, route: str = "jieba") -> SearchHit:
    return SearchHit(
        doc_id=doc_id, mount_id=1, title="t%d" % doc_id, rel_path="%d.md" % doc_id,
        abs_path="", format="md", score=1.0, chunk_seq=seq,
        snippet_html="", snippet_text="", route=route,
    )


class ParseTest(unittest.TestCase):
    def test_parses_numbered_lines(self) -> None:
        text = "1. 文件锁 加锁\n2、fcntl 文件锁\n3) 文件锁怎么用"
        variants = parse_rewrites(text, "文件锁怎么用", 3)
        self.assertEqual(variants, ["文件锁 加锁", "fcntl 文件锁"])

    def test_dedup_and_limits(self) -> None:
        text = "文件锁\n文件锁\n加锁\nfcntl"
        variants = parse_rewrites(text, "问题", 2)
        self.assertEqual(len(variants), 2)


class StoreTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = QueryExpandStore(Path(tmp))
            cfg = QueryExpandConfig(enabled=True, max_queries=4, system_prompt="自定义提示词")
            store.save(cfg)
            loaded = store.load()
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.max_queries, 4)
            self.assertEqual(loaded.system_prompt, "自定义提示词")


class RewriterTest(unittest.TestCase):
    def test_cache_and_parse(self) -> None:
        cfg = QueryExpandConfig(enabled=True, max_queries=2, timeout_s=2)
        ai = mock.Mock()
        ai.ready = True
        rewriter = QueryRewriter(cfg, ai)
        with mock.patch("ai.query_rewrite.LLMClient") as client_cls:
            client_cls.return_value.chat.return_value = {"text": "文件锁 加锁\nfcntl 文件锁"}
            first = rewriter.rewrite("文件锁怎么用")
            second = rewriter.rewrite("文件锁怎么用")
        self.assertEqual(first, ["文件锁 加锁", "fcntl 文件锁"])
        self.assertEqual(second, first)
        self.assertEqual(client_cls.return_value.chat.call_count, 1)

    def test_custom_prompt_is_sent(self) -> None:
        cfg = QueryExpandConfig(enabled=True, max_queries=2, system_prompt="我的自定义提示")
        ai = mock.Mock()
        ai.ready = True
        rewriter = QueryRewriter(cfg, ai)
        with mock.patch("ai.query_rewrite.LLMClient") as client_cls:
            client_cls.return_value.chat.return_value = {"text": "文件锁"}
            rewriter.rewrite("文件锁怎么用")
        messages = client_cls.return_value.chat.call_args[0][0]
        self.assertEqual(messages[0]["content"], "我的自定义提示")

    def test_disabled_returns_empty(self) -> None:
        cfg = QueryExpandConfig(enabled=False)
        ai = mock.Mock()
        ai.ready = True
        self.assertEqual(QueryRewriter(cfg, ai).rewrite("问题"), [])


class RrfMergeTest(unittest.TestCase):
    def test_expanded_appends_without_reordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
            try:
                base_hits = [make_hit(1), make_hit(2)]
                variant_hits = [make_hit(2), make_hit(3)]
                merged = agent._merge_expanded([base_hits, variant_hits], topk=5)
                self.assertEqual([h.doc_id for h in merged], [1, 2, 3])
                self.assertEqual(merged[-1].route, "expanded")
            finally:
                agent.stop()

    def test_merge_and_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
            try:
                base_hits = [make_hit(1), make_hit(2)]
                variant_hits = [make_hit(2), make_hit(3)]
                merged = agent._rrf_merge([base_hits, variant_hits], topk=5)
                ids = [h.doc_id for h in merged]
                self.assertEqual(ids, [2, 1, 3])
                self.assertEqual(merged[0].route, "jieba")
                self.assertEqual(merged[-1].route, "expanded")
            finally:
                agent.stop()


if __name__ == "__main__":
    unittest.main()
