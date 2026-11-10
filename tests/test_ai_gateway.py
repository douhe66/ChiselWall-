"""自定义网关：模板、文件占位符、请求头、URL 拼接。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.llm import (
    AIConfig,
    LLMClient,
    LLMError,
    request_headers,
    request_urls,
    resolve_gateway_base,
)


class GatewayHandler(BaseHTTPRequestHandler):
    seen_path = ""
    seen_headers = {}

    def do_POST(self):
        GatewayHandler.seen_path = self.path
        GatewayHandler.seen_headers = dict(self.headers)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({
            "model": "stub",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class GatewayUrlTest(unittest.TestCase):
    def test_host_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host.txt"
            host.write_text("http://bai.com/\n", encoding="utf-8")
            self.assertEqual(
                resolve_gateway_base("${host}/inf/v1", {"host": str(host)}),
                "http://bai.com/inf/v1",
            )

    def test_path_placeholder_and_duplicate_slashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "path.txt"
            path.write_text("/inf/v1\n", encoding="utf-8")
            self.assertEqual(
                resolve_gateway_base("http://bai.com/${path}", {"path": str(path)}),
                "http://bai.com/inf/v1",
            )

    def test_two_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host.txt"
            path = Path(tmp) / "path.txt"
            host.write_text("http://bai.com/", encoding="utf-8")
            path.write_text("/inf/v1", encoding="utf-8")
            cfg = AIConfig(
                enabled=True, mode="gateway", base_url="${host}${path}", model="stub",
                placeholder_files={"host": str(host), "path": str(path)},
            )
            self.assertEqual(request_urls(cfg), ["http://bai.com/inf/v1/chat/completions"])

    def test_direct_gateway_url_without_placeholder(self):
        cfg = AIConfig(enabled=True, mode="gateway", base_url="http://bai.com/inf/v1", model="stub")
        self.assertEqual(request_urls(cfg), ["http://bai.com/inf/v1/chat/completions"])

    def test_full_endpoint_not_duplicated(self):
        cfg = AIConfig(enabled=True, mode="gateway", base_url="http://bai.com/inf/v1/chat/completions", model="stub")
        self.assertEqual(request_urls(cfg), ["http://bai.com/inf/v1/chat/completions"])

    def test_missing_file(self):
        cfg = AIConfig(enabled=True, mode="gateway", base_url="${host}/v1", model="stub")
        with self.assertRaises(LLMError) as ctx:
            request_urls(cfg)
        self.assertIn("未配置文件路径", str(ctx.exception))


class HeaderTest(unittest.TestCase):
    def test_custom_headers_and_auth_override(self):
        cfg = AIConfig(
            enabled=True, mode="gateway", base_url="http://bai.com/inf/v1", model="stub", api_key="key",
            headers=[
                {"name": "X-Tenant", "value": "dept"},
                {"name": "Authorization", "value": "Custom token"},
                {"name": "Content-Type", "value": "text/plain"},
            ],
        )
        headers = request_headers(cfg)
        self.assertEqual(headers["Authorization"], "Custom token")
        self.assertEqual(headers["X-Tenant"], "dept")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_merge_blank_keeps_old(self):
        from ai.llm import merge_headers

        old = [{"name": "X-Key", "value": "secret"}]
        merged = merge_headers(old, [{"name": "X-Key", "value": ""}])
        self.assertEqual(merged, [{"name": "X-Key", "value": "secret"}])


class GatewayClientTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_direct_url_and_headers(self):
        cfg = AIConfig(
            enabled=True, mode="gateway",
            base_url="http://127.0.0.1:%d/inf/v1" % self.port, model="stub",
            headers=[{"name": "X-Tenant", "value": "dept"}],
        )
        result = LLMClient(cfg).chat([{"role": "user", "content": "hi"}], timeout_s=5)
        self.assertEqual(result["text"], "ok")
        self.assertEqual(GatewayHandler.seen_path, "/inf/v1/chat/completions")
        self.assertEqual(GatewayHandler.seen_headers.get("X-Tenant"), "dept")


if __name__ == "__main__":
    unittest.main()
