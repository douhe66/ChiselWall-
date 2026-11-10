"""OpenAI 兼容 LLM 客户端（M5，设计 §10）。

支持任意 OpenAI 兼容端点：DeepSeek（https://api.deepseek.com）、Ollama
（http://127.0.0.1:11434/v1）、公司内网模型（自定义 base_url）。
配置持久化在配置目录 ai.json（api_key 不写日志、不回显明文）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("chiselwall.ai")


DEFAULT_SYSTEM_PROMPT = (
    "你是「凿壁」知识助手。只依据用户提供的资料片段回答，不得编造资料之外的事实。"
    "回答时用方括号编号标注依据，如 [1]、[2]；资料不足时明确说明哪些方面没有找到，不要猜测。"
    "使用 Markdown 输出：分点、代码块、表格按需使用，便于阅读。"
)


class LLMError(Exception):
    """LLM 调用失败（连接/鉴权/超时/返回格式）。"""


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def parse_placeholders(template: str) -> list[str]:
    """按出现顺序返回 base_url 模板中的 ${name}（去重）。"""
    out: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(template or ""):
        name = match.group(1)
        if name not in out:
            out.append(name)
    return out


def _normalize_placeholder_files(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        name = str(k or "").strip()
        value = str(v or "").strip()
        if name and value:
            out[name] = value
    return out


def _normalize_headers(raw: Any) -> list[dict[str, str]]:
    """接受 [{name,value}] 或 {name: value}，返回去重后的列表。"""
    if isinstance(raw, dict):
        raw = [{"name": k, "value": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value:
            continue
        low = name.lower()
        if low in ("content-type",) or low in seen:
            continue
        if not _HEADER_NAME_RE.match(name) or "\r" in value or "\n" in value:
            continue
        seen.add(low)
        out.append({"name": name, "value": value})
    return out


@dataclass
class AIConfig:
    enabled: bool = False
    base_url: str = ""          # 例：https://api.deepseek.com 或 http://内网:8000/v1
    api_key: str = ""
    model: str = ""
    temperature: float = 0.2
    timeout_s: float = 120.0
    max_context_chars: int = 24000  # 上下文字符预算（长文档/多文档）
    top_n: int = 24                 # 送入模型的最大片段数
    use_system_proxy: bool = False  # 默认直连（内网模型）；访问外网模型可开启
    max_tokens: int = 4000          # 回答长度上限（流式输出，长答案不再被截断）
    system_prompt: str = ""         # 留空 = 使用内置默认提示词
    # 自定义网关：base_url 为模板；占位符 -> 本地文件路径（每次请求读取）
    mode: str = "openai"            # openai | gateway
    placeholder_files: dict[str, str] = field(default_factory=dict)
    headers: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AIConfig":
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in allowed})
        cfg.mode = "gateway" if str(cfg.mode).lower() == "gateway" else "openai"
        cfg.placeholder_files = _normalize_placeholder_files(cfg.placeholder_files)
        cfg.headers = _normalize_headers(cfg.headers)
        return cfg

    def public_dict(self) -> dict[str, Any]:
        """返回给前端的配置（api_key 只显示是否已设置与掩码）。"""
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "mode": self.mode,
            "placeholders": parse_placeholders(self.base_url) if self.mode == "gateway" else [],
            "placeholder_files": dict(self.placeholder_files),
            "headers": [
                {"name": h.get("name", ""), "has_value": bool(h.get("value")),
                 "masked_value": ("****" + str(h.get("value"))[-4:]) if len(str(h.get("value") or "")) > 4 else ("****" if h.get("value") else "")}
                for h in self.headers
            ],
            "model": self.model,
            "temperature": self.temperature,
            "has_key": bool(self.api_key),
            "masked_key": ("****" + self.api_key[-4:]) if len(self.api_key) > 4 else "",
            "ready": self.ready,
            "use_system_proxy": self.use_system_proxy,
            "system_prompt": self.system_prompt,
            "default_system_prompt": DEFAULT_SYSTEM_PROMPT,
            "max_context_chars": self.max_context_chars,
            "top_n": self.top_n,
            "max_tokens": self.max_tokens,
        }

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.base_url.strip() and self.model.strip())


class AIConfigStore:
    """ai.json 读写（api_key 落盘在本机配置目录）。"""

    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "ai.json"
        self.config_dir = config_dir

    def load(self) -> AIConfig:
        if not self.path.exists():
            return AIConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取 ai.json 失败，使用默认配置")
            return AIConfig()
        if not isinstance(data, dict):
            return AIConfig()
        cfg = AIConfig.from_dict(data)
        # 旧版默认 8000/2000 会自动升到更长上下文与回答预算；用户可再手动调小。
        if cfg.max_context_chars == 8000:
            cfg.max_context_chars = 24000
        if cfg.max_tokens == 2000:
            cfg.max_tokens = 4000
        return cfg

    def save(self, cfg: AIConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_base_url(url: str) -> str:
    """规范化 base_url：去空白/尾部斜杠；误填完整接口路径时剥离。"""
    u = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/v1/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u


def _chat_candidates(base: str) -> list[str]:
    """chat 接口候选 URL：兼容「带 /v1」与「不带 /v1」两种部署习惯。"""
    base = normalize_base_url(base)
    if not base:
        return []
    variants = [base]
    if base.endswith("/v1"):
        variants.append(base[: -len("/v1")].rstrip("/"))
    else:
        variants.append(base + "/v1")
    out: list[str] = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out


def _placeholder_config_dir() -> Path:
    return Path(os.environ.get("CHISELWALL_CONFIG_DIR", "~/.chiselwall")).expanduser()


def _read_placeholder_value(name: str, raw_path: str) -> str:
    if not raw_path:
        raise LLMError("占位符 ${%s} 未配置文件路径" % name)
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = _placeholder_config_dir() / p
    try:
        value = p.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LLMError("读取占位符 ${%s} 失败（%s）：%s" % (name, p, exc)) from exc
    if not value:
        raise LLMError("占位符 ${%s} 的文件为空：%s" % (name, p))
    if len(value) > 4096:
        raise LLMError("占位符 ${%s} 的文件内容过长（>4096 字符）：%s" % (name, p))
    if "\n" in value or "\r" in value or "\x00" in value:
        raise LLMError("占位符 ${%s} 的文件内容不能包含换行/控制字符：%s" % (name, p))
    return value


def _collapse_url_slashes(url: str) -> str:
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return scheme + "://" + re.sub(r"/{2,}", "/", rest)


def resolve_gateway_base(template: str, placeholder_files: dict[str, str]) -> str:
    """读取占位符文件并替换 base_url 模板；无占位符时原样返回。"""
    base = (template or "").strip()
    if not base:
        raise LLMError("自定义网关的 base_url 为空")
    for name in parse_placeholders(base):
        value = _read_placeholder_value(name, str((placeholder_files or {}).get(name) or ""))
        base = base.replace("${" + name + "}", value)
    if "${" in base:
        raise LLMError("base_url 中仍有未解析的占位符：" + base)
    base = _collapse_url_slashes(base).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise LLMError("自定义网关解析后的 base_url 不是合法 http(s) 地址：" + base)
    return base


def request_urls(cfg: AIConfig) -> list[str]:
    """标准模式保留 /v1 兼容；网关模式使用模板解析后的精确前缀。"""
    if cfg.mode == "gateway":
        base = resolve_gateway_base(cfg.base_url, cfg.placeholder_files)
        if base.endswith("/chat/completions"):
            return [base]
        return [base + "/chat/completions"]
    return [base + "/chat/completions" for base in _chat_candidates(cfg.base_url)]


def merge_headers(existing: list[dict[str, str]], incoming: Any) -> list[dict[str, str]]:
    """UI 空白值表示保留同名旧值；删除行表示删除；空列表表示清空。"""
    old = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in (existing or [])}
    if isinstance(incoming, dict):
        incoming = [{"name": k, "value": v} for k, v in incoming.items()]
    if not isinstance(incoming, list):
        return list(existing or [])
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in incoming:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        low = name.lower()
        if not value:
            value = old.get(low, "")
        if not name or not value or low in seen or low == "content-type":
            continue
        if not _HEADER_NAME_RE.match(name) or "\r" in value or "\n" in value:
            continue
        seen.add(low)
        merged.append({"name": name, "value": value})
    return merged


def request_headers(cfg: AIConfig) -> dict[str, str]:
    """默认 JSON + Bearer；自定义头覆盖默认值（Authorization 显式优先）。"""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = "Bearer " + cfg.api_key
    for item in cfg.headers or []:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value:
            continue
        if not _HEADER_NAME_RE.match(name) or "\r" in value or "\n" in value:
            raise LLMError("非法请求头：" + name)
        if name.lower() == "content-type":
            continue
        if name.lower() == "authorization":
            headers.pop("Authorization", None)
            headers.pop("authorization", None)
        headers[name] = value
    return headers


class LLMClient:
    """OpenAI 兼容 /chat/completions 客户端（同步，供线程池调用）。"""

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg

    def chat(self, messages: list[dict], *, max_tokens: Optional[int] = None, timeout_s: Optional[float] = None) -> dict:
        """返回 {text, model, elapsed_ms, usage}；失败抛 LLMError。"""
        if not self.cfg.ready:
            raise LLMError("AI 未配置：请在设置页填写 base_url / model（以及 api_key）")
        urls = request_urls(self.cfg)
        if not urls:
            raise LLMError("AI base_url 为空")
        headers = request_headers(self.cfg)
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": float(self.cfg.temperature),
            "max_tokens": int(max_tokens or self.cfg.max_tokens),
            "stream": False,
        }
        started = time.monotonic()
        resp = None
        attempted: list[str] = []
        client = httpx.Client(
            timeout=float(timeout_s or self.cfg.timeout_s),
            trust_env=self.cfg.use_system_proxy,
        )
        try:
            for idx, url in enumerate(urls):
                attempted.append(url)
                try:
                    resp = client.post(url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    raise LLMError("无法连接模型服务 " + url + ": " + str(exc)) from exc
                if resp.status_code in (404, 405) and idx < len(urls) - 1:
                    continue  # 路径不对：换另一种 base_url 形态重试
                break
        finally:
            client.close()
        elapsed = int((time.monotonic() - started) * 1000)
        if resp is None:
            raise LLMError("模型服务无响应（尝试: " + ", ".join(attempted) + "）")
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise LLMError(
                "模型服务返回 %s（请求 %s）: %s" % (resp.status_code, attempted[-1], detail)
            )
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("模型返回格式不符合 OpenAI 兼容协议: " + str(exc)) from exc
        return {
            "text": str(text or "").strip(),
            "model": str(data.get("model") or self.cfg.model),
            "elapsed_ms": elapsed,
            "usage": data.get("usage") or {},
        }

    def chat_stream(self, messages: list[dict], *, max_tokens: Optional[int] = None, timeout_s: Optional[float] = None):
        """流式对话：逐段 yield 文本增量；失败抛 LLMError。"""
        if not self.cfg.ready:
            raise LLMError("AI 未配置：请在设置页填写 base_url / model（以及 api_key）")
        urls = request_urls(self.cfg)
        if not urls:
            raise LLMError("AI base_url 为空")
        headers = request_headers(self.cfg)
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": float(self.cfg.temperature),
            "max_tokens": int(max_tokens or self.cfg.max_tokens),
            "stream": True,
        }
        client = httpx.Client(
            timeout=float(timeout_s or self.cfg.timeout_s),
            trust_env=self.cfg.use_system_proxy,
        )
        try:
            for idx, url in enumerate(urls):
                try:
                    with client.stream("POST", url, json=payload, headers=headers) as resp:
                        if resp.status_code in (404, 405) and idx < len(urls) - 1:
                            continue
                        if resp.status_code >= 400:
                            body = resp.read().decode("utf-8", "ignore")[:300]
                            raise LLMError("模型服务返回 %s（请求 %s）: %s" % (resp.status_code, url, body))
                        for line in resp.iter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            try:
                                obj = json.loads(data)
                                delta = obj["choices"][0].get("delta") or {}
                            except (ValueError, KeyError, IndexError, TypeError):
                                continue
                            text = delta.get("content")
                            if text:
                                yield str(text)
                        return
                except httpx.HTTPError as exc:
                    raise LLMError("无法连接模型服务 " + url + ": " + str(exc)) from exc
        finally:
            client.close()

    def ping(self) -> dict:
        """连通性测试：最小对话一次。"""
        return self.chat([{"role": "user", "content": "你好，请回复 ok"}], max_tokens=8, timeout_s=20)