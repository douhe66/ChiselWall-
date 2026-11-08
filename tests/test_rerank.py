"""P1 重排框架单测：HTTP 协议适配、配置、rerank_hits 排序与回退。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.rerank import HttpReranker, LocalCrossEncoderReranker, RerankConfig, RerankConfigStore
from node.agent import Config, NodeAgent


class RerankStub(BaseHTTPRequestHandler):
    """同时兼容 cohere/jina（documents）与 TEI（texts）两种请求。"""
    mode = "cohere"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        docs = data.get("documents") or data.get("texts") or []
        scores = [float(2.0 if "文件锁" in d else 0.1) for d in docs]
        if "texts" in data:
            body = json.dumps([{"index": i, "score": s} for i, s in enumerate(scores)]).encode()
        else:
            body = json.dumps({"results": [
                {"index": i, "relevance_score": s} for i, s in enumerate(scores)
            ]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def start_stub() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), RerankStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


class HttpRerankerTest(unittest.TestCase):
    def test_cohere_style(self) -> None:
        srv, base = start_stub()
        try:
            cfg = RerankConfig(enabled=True, provider="http", base_url=base, api_style="cohere")
            scores = HttpReranker(cfg).rerank("文件锁怎么用", ["fcntl 文件锁示例", "Vue 组件通信"])
            self.assertEqual(scores[0], 2.0)
            self.assertEqual(scores[1], 0.1)
        finally:
            srv.shutdown()

    def test_tei_style(self) -> None:
        srv, base = start_stub()
        try:
            cfg = RerankConfig(enabled=True, provider="http", base_url=base, api_style="tei")
            scores = HttpReranker(cfg).rerank("文件锁", ["Vue", "文件锁说明"])
            self.assertEqual(scores[1], 2.0)
        finally:
            srv.shutdown()

    def test_auto_style(self) -> None:
        srv, base = start_stub()
        try:
            cfg = RerankConfig(enabled=True, provider="http", base_url=base, api_style="auto")
            scores = HttpReranker(cfg).rerank("文件锁", ["文件锁 A", "B"])
            self.assertEqual(len(scores), 2)
            self.assertGreater(scores[0], scores[1])
        finally:
            srv.shutdown()


class ConfigTest(unittest.TestCase):
    def test_roundtrip_and_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RerankConfigStore(Path(tmp))
            cfg = RerankConfig(enabled=True, provider="local", model_dir=str(Path(tmp) / "m"), api_key="secret-1234")
            store.save(cfg)
            loaded = store.load()
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.api_key, "secret-1234")
            pub = loaded.public_dict()
            self.assertTrue(pub["has_key"])
            self.assertNotIn("secret", json.dumps(pub))

    def test_local_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RerankConfig(enabled=True, provider="local", model_dir=tmp)
            self.assertFalse(LocalCrossEncoderReranker(cfg).ready)


class RerankHitsTest(unittest.TestCase):
    class FakeReranker:
        ready = True
        name = "fake"

        def rerank(self, query, docs):
            return [float(10 - i) for i in range(len(docs))]

    def test_reorders_and_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
            try:
                agent.reranker = self.FakeReranker()  # type: ignore[assignment]
                agent.rerank_config.enabled = True
                agent.rerank_config.max_candidates = 5
                agent.rerank_config.top_k = 3
                hits = [
                    {"title": "a", "snippet_text": "aaa"},
                    {"title": "b", "snippet_text": "bbb"},
                    {"title": "c", "snippet_text": "ccc"},
                ]
                out = agent.rerank_hits("q", hits)
                self.assertEqual([h["title"] for h in out], ["a", "b", "c"])
                self.assertTrue(all(h.get("reranked") for h in out))
            finally:
                agent.stop()

    def test_disabled_returns_same(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
            try:
                hits = [{"title": "a"}, {"title": "b"}]
                self.assertEqual(agent.rerank_hits("q", hits), hits)
            finally:
                agent.stop()


class CacheAndUnloadTest(unittest.TestCase):
    class CountingReranker:
        ready = True
        name = "count"

        def __init__(self) -> None:
            self.calls = 0

        def rerank(self, query, docs):
            self.calls += 1
            return [float(10 - i) for i in range(len(docs))]

    def test_cache_hit_avoids_second_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
            try:
                fake = self.CountingReranker()
                agent.reranker = fake  # type: ignore[assignment]
                agent.rerank_config.enabled = True
                agent.rerank_config.top_k = 3
                agent.rerank_config.cache_size = 10
                hits = [
                    {"title": "a", "snippet_text": "aaa"},
                    {"title": "b", "snippet_text": "bbb"},
                ]
                first = agent.rerank_hits("同一个问题", hits)
                second = agent.rerank_hits("同一个问题", hits)
                self.assertEqual(fake.calls, 1)
                self.assertEqual([h["title"] for h in first], [h["title"] for h in second])
            finally:
                agent.stop()

    def test_idle_unload_clears_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RerankConfig(enabled=True, provider="local", model_dir=tmp, idle_unload_s=1)
            r = LocalCrossEncoderReranker(cfg)
            r._sess = object()
            r._tok = object()
            r._np = object()
            r._last_use = 0.0
            r._maybe_unload()
            self.assertIsNone(r._sess)


if __name__ == "__main__":
    unittest.main()