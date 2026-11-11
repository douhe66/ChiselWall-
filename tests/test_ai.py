"""M5 AI 层单测：RAG 上下文构建/预算、LLM 客户端（本地假服务）、配置掩码、API 端点。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.llm import (
    AIConfig,
    AIConfigStore,
    LLMClient,
    LLMError,
    _chat_candidates,
    normalize_base_url,
)
from ai.rag import answer_question, build_context, build_messages, select_local_window


class StubLLM(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容 /chat/completions 假服务。"""
    reply = "这是来自假模型的回答 [1]。"
    status = 200

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if StubLLM.status != 200:
            self.send_response(StubLLM.status)
            self.end_headers()
            self.wfile.write(b'{"error":"boom"}')
            return
        body = json.dumps({
            "model": "stub-model",
            "choices": [{"message": {"role": "assistant", "content": StubLLM.reply}}],
            "usage": {"total_tokens": 10},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静默
        return


def start_stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubLLM)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    port = srv.server_address[1]
    return srv, "http://127.0.0.1:%d/v1" % port


def make_hit(i: int, *, title: str = "", snippet: str = "", nodes=None) -> dict:
    return {
        "title": title or "笔记%d" % i,
        "rel_path": "notes/%d.md" % i,
        "format": "md",
        "snippet_text": snippet or ("片段内容%d：" % i + "x" * 50),
        "snippet_html": snippet or ("片段内容%d" % i),
        "route": "jieba",
        "nodes": nodes or ["bob"],
        "agreement": 1,
    }


class BuildContextTest(unittest.TestCase):
    def test_numbering_and_citations(self) -> None:
        hits = [make_hit(1), make_hit(2), make_hit(3)]
        ctx, cites = build_context(hits, max_chars=8000, top_n=20)
        self.assertEqual(len(cites), 3)
        self.assertIn("[1] 《笔记1》", ctx)
        self.assertIn("[3] 《笔记3》", ctx)
        self.assertEqual(cites[0]["index"], 1)
        self.assertEqual(cites[1]["rel_path"], "notes/2.md")

    def test_budget_trims(self) -> None:
        hits = [make_hit(i, snippet="y" * 200) for i in range(1, 20)]
        ctx, cites = build_context(hits, max_chars=600, top_n=20)
        self.assertLessEqual(len(ctx), 1000)
        self.assertLess(len(cites), 20)
        self.assertGreaterEqual(len(cites), 1)

    def test_top_n_cap(self) -> None:
        hits = [make_hit(i) for i in range(1, 30)]
        _ctx, cites = build_context(hits, top_n=5)
        self.assertEqual(len(cites), 5)

    def test_skip_empty_snippets(self) -> None:
        hits = [{"title": "空", "snippet_text": "", "snippet_html": "", "rel_path": "a"}]
        ctx, cites = build_context(hits)
        self.assertEqual(cites, [])
        self.assertEqual(ctx, "")


class LocalWindowTest(unittest.TestCase):
    """本地命中要拿到含代码块的完整章节，而不是 150 字片段。"""

    def test_window_includes_neighbors(self) -> None:
        chunks = ["前言", "fcntl 简介", "示例代码 struct flock", "释放锁", "后记"]
        text = select_local_window(chunks, 2)
        self.assertIn("fcntl 简介", text)
        self.assertIn("示例代码", text)
        self.assertIn("释放锁", text)

    def test_empty(self) -> None:
        self.assertEqual(select_local_window([], 0), "")

    def test_budget(self) -> None:
        chunks = ["x" * 2000 for _ in range(5)]
        text = select_local_window(chunks, 2, max_chars=3000)
        self.assertLessEqual(len(text), 3000)

    def test_context_full_uses_longer_text(self) -> None:
        long_snippet = "代码块开始 " + "y" * 1500 + " 代码块结束"
        hit = dict(make_hit(1), snippet_text=long_snippet, context_full=True)
        ctx, _ = build_context([hit], max_chars=8000)
        self.assertIn("代码块结束", ctx)

    def test_remote_ai_text_is_used(self) -> None:
        hit = dict(make_hit(1), snippet_text="很短的片段", ai_text="远端节点补充的完整上下文")
        ctx, cites = build_context([hit], max_chars=8000)
        self.assertIn("远端节点补充的完整上下文", ctx)
        self.assertNotIn("很短的片段", ctx)
        self.assertEqual(len(cites), 1)

    def test_weak_hit_is_labeled(self) -> None:
        hit = dict(make_hit(1), tier="weak")
        ctx, _ = build_context([hit], max_chars=8000)
        self.assertIn("弱相关", ctx)


class RagAnswerTest(unittest.TestCase):
    def test_unconfigured(self) -> None:
        result = answer_question("问题", [make_hit(1)], AIConfig())
        self.assertEqual(result["status"], "unconfigured")
        self.assertIn("未配置", result["error"])

    def test_answer_with_stub(self) -> None:
        srv, base = start_stub()
        try:
            cfg = AIConfig(enabled=True, base_url=base, model="stub-model", api_key="k")
            result = answer_question("fcntl 怎么加锁", [make_hit(1, title="文件锁")], cfg)
            self.assertEqual(result["status"], "ready")
            self.assertIn("假模型的回答", result["answer"])
            self.assertEqual(len(result["citations"]), 1)
        finally:
            srv.shutdown()

    def test_error_surfaced(self) -> None:
        srv, base = start_stub()
        StubLLM.status = 500
        try:
            cfg = AIConfig(enabled=True, base_url=base, model="m")
            result = answer_question("q", [make_hit(1)], cfg)
            self.assertEqual(result["status"], "error")
            self.assertIn("500", result["error"])
        finally:
            StubLLM.status = 200
            srv.shutdown()


class BaseUrlNormalizeTest(unittest.TestCase):
    def test_strips_endpoint_path(self) -> None:
        self.assertEqual(normalize_base_url("https://api.deepseek.com/chat/completions"), "https://api.deepseek.com")
        self.assertEqual(
            normalize_base_url(" https://api.deepseek.com/v1/chat/completions/ "),
            "https://api.deepseek.com/v1",
        )

    def test_candidates(self) -> None:
        self.assertEqual(
            _chat_candidates("https://api.deepseek.com"),
            ["https://api.deepseek.com", "https://api.deepseek.com/v1"],
        )
        self.assertEqual(
            _chat_candidates("https://api.deepseek.com/v1"),
            ["https://api.deepseek.com/v1", "https://api.deepseek.com"],
        )


class RetryVariantTest(unittest.TestCase):
    """404 时自动换 base_url 形态重试（DeepSeek 常见 /v1 差异）。"""

    def test_retry_on_404(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if self.path != "/v1/chat/completions":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"model": "m", "choices": [{"message": {"content": "ok"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                return

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            cfg = AIConfig(enabled=True, base_url="http://127.0.0.1:%d" % port, model="m")
            result = LLMClient(cfg).chat([{"role": "user", "content": "hi"}])
            self.assertEqual(result["text"], "ok")
        finally:
            srv.shutdown()


class PromptTest(unittest.TestCase):
    def test_build_messages_default_prompt(self) -> None:
        msgs = build_messages("问题", "资料")
        self.assertIn("只依据", msgs[0]["content"])

    def test_build_messages_custom_prompt(self) -> None:
        msgs = build_messages("问题", "资料", "你是自定义助手，请用英文回答。")
        self.assertEqual(msgs[0]["content"], "你是自定义助手，请用英文回答。")


class StoreTest(unittest.TestCase):
    def test_roundtrip_and_mask(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = AIConfigStore(Path(tmp))
            cfg = AIConfig(enabled=True, base_url="http://x/v1", model="m", api_key="secret-1234")
            store.save(cfg)
            loaded = store.load()
            self.assertEqual(loaded.api_key, "secret-1234")
            self.assertEqual(loaded.system_prompt, "")
            pub = loaded.public_dict()
            self.assertTrue(pub["has_key"])
            self.assertEqual(pub["masked_key"], "****1234")
            self.assertNotIn("secret", json.dumps(pub))


class AiTestEndpointTest(unittest.TestCase):
    """POST /api/ai/test 使用表单未保存值（无需先保存）；缺失字段给出明确错误。"""

    def setUp(self) -> None:
        import tempfile

        from fastapi.testclient import TestClient

        from api.server import create_app
        from node.agent import Config, NodeAgent

        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
        self.client = TestClient(create_app(self.agent, token="t"))

    def tearDown(self) -> None:
        self.agent.stop()
        self.tmp.cleanup()

    def test_unsaved_values_are_used(self) -> None:
        srv, base = start_stub()
        try:
            r = self.client.post(
                "/api/ai/test",
                json={"base_url": base, "model": "stub-model", "api_key": "k"},
                headers={"X-Chisel-Token": "t"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["ok"])
        finally:
            srv.shutdown()

    def test_missing_fields_error(self) -> None:
        r = self.client.post("/api/ai/test", json={}, headers={"X-Chisel-Token": "t"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("base_url", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
