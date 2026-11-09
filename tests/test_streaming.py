"""流式 AI 与上下文预算单测。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.llm import AIConfig, AIConfigStore, LLMClient
from ai.rag import build_context, stream_answer


class StreamHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ["流式", "回答", " [1]"]:
            data = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        return


class StreamTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), StreamHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_chat_stream(self):
        cfg = AIConfig(enabled=True, base_url=self.url, model="stub")
        text = "".join(LLMClient(cfg).chat_stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(text, "流式回答 [1]")

    def test_stream_answer_events(self):
        cfg = AIConfig(enabled=True, base_url=self.url, model="stub", max_context_chars=12000)
        hits = [{"title": "笔记", "rel_path": "a.md", "format": "md",
                 "snippet_text": "资料", "snippet_html": "资料", "nodes": ["bob"], "route": "jieba"}]
        events = list(stream_answer("问题", hits, cfg))
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("流式", "".join(e.get("text", "") for e in events if e["type"] == "delta"))
        self.assertEqual(events[-1]["text"], "流式回答 [1]")

    def test_context_full_doc_not_hard_capped_at_5000(self):
        hits = [{"title": "长文", "rel_path": "long.md", "format": "md",
                 "snippet_text": "x" * 9000, "snippet_html": "", "context_full": True,
                 "nodes": ["我"], "route": "jieba"}]
        ctx, cites = build_context(hits, max_chars=12000)
        self.assertGreater(len(ctx), 5000)
        self.assertLessEqual(len(ctx), 12000)
        self.assertEqual(len(cites), 1)


class ConfigMigrationTest(unittest.TestCase):
    def test_old_defaults_upgraded(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            p = Path(tmp) / "ai.json"
            p.write_text(json.dumps({"max_context_chars": 8000, "max_tokens": 2000}), encoding="utf-8")
            cfg = AIConfigStore(Path(tmp)).load()
            self.assertEqual(cfg.max_context_chars, 24000)
            self.assertEqual(cfg.max_tokens, 4000)




class CitationPersistenceTest(unittest.TestCase):
    def test_stream_done_writes_citations(self):
        import tempfile
        from unittest import mock

        from node.agent import Config, NodeAgent

        with tempfile.TemporaryDirectory() as tmp:
            agent = NodeAgent(Config(db_path=Path(tmp) / "c.db", config_dir=Path(tmp) / "cfg"))
            try:
                s = agent.ask_session_create("citations")
                agent.ask_sessions.append(
                    s["id"], "user", "问题",
                    {"aggregated": [{"title": "笔记", "rel_path": "a.md", "format": "md",
                                     "snippet_text": "资料", "snippet_html": "资料", "nodes": ["我"]}]},
                )

                def fake_stream(question, aggregated, cfg, history=None):
                    yield {"type": "start", "citations": [{"index": 1, "title": "笔记"}], "context_chars": 10, "model": "stub"}
                    yield {"type": "delta", "text": "回答"}
                    yield {"type": "done", "text": "回答", "citations": [{"index": 1, "title": "笔记"}],
                           "context_chars": 10, "model": "stub", "elapsed_ms": 5}

                with mock.patch("ai.rag.stream_answer", fake_stream):
                    list(agent.ask_session_summarize_stream(s["id"]))
                turns = agent.ask_sessions.turns(s["id"])
                self.assertEqual(turns[-1]["role"], "assistant")
                self.assertEqual(turns[-1]["payload"]["citations"][0]["title"], "笔记")
            finally:
                agent.stop()




class LongDocContextTest(unittest.TestCase):
    def test_long_doc_uses_head_window_and_tail(self):
        from types import SimpleNamespace
        from ai.llm import AIConfig
        from node.agent import NodeAgent

        class FakeDB:
            def get_chunk_texts(self, _doc_id):
                return ["A" * 8000, "B" * 8000, "C" * 8000]

        agent = SimpleNamespace(
            db=FakeDB(),
            config=SimpleNamespace(relay_nick="我"),
            ai_config=AIConfig(max_context_chars=24000),
        )
        hits = [{
            "title": "长文", "rel_path": "long.md", "format": "md",
            "snippet_text": "短片段", "snippet_html": "", "chunk_seq": 1,
            "sources": [{"node": "我", "doc_id": 1}], "nodes": ["我"],
        }]
        out = NodeAgent.enrich_local_context(agent, hits)
        text = out[0]["snippet_text"]
        self.assertIn("中间省略", text)
        self.assertFalse(out[0]["context_full_doc"])
        self.assertGreater(len(text), 12000)
        self.assertLessEqual(len(text), 14000)


if __name__ == "__main__":
    unittest.main()