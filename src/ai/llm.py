"""OpenAI 兼容 LLM 客户端（M5，设计 §10）。

支持任意 OpenAI 兼容端点：DeepSeek（https://api.deepseek.com）、Ollama
（http://127.0.0.1:11434/v1）、公司内网模型（自定义 base_url）。
配置持久化在配置目录 ai.json（api_key 不写日志、不回显明文）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Optional

import httpx

log = logging.getLogger("chiselwall.ai")


DEFAULT_SYSTEM_PROMPT = (
    "你是「凿壁」知识助手。只依据用户提供的资料片段回答，不得编造资料之外的事实。"
    "回答时用方括号编号标注依据，如 [1]、[2]；资料不足时明确说明哪些方面没有找到，不要猜测。"
    "使用 Markdown 输出：分点、代码块、表格按需使用，便于阅读。"
)


class LLMError(Exception):
    """LLM 调用失败（连接/鉴权/超时/返回格式）。"""


@dataclass
class AIConfig:
    enabled: bool = False
    base_url: str = ""          # 例：https://api.deepseek.com 或 http://内网:8000/v1
    api_key: str = ""
    model: str = ""
    temperature: float = 0.2
    timeout_s: float = 60.0
    max_context_chars: int = 8000   # 上下文字符预算（约 4K-8K token，按模型可调）
    top_n: int = 20                 # 送入模型的最大片段数
    use_system_proxy: bool = False  # 默认直连（内网模型）；访问外网模型可开启
    max_tokens: int = 2000          # 回答长度上限（含代码块，800 会截断长答案）
    system_prompt: str = ""         # 留空 = 使用内置默认提示词

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AIConfig":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def public_dict(self) -> dict[str, Any]:
        """返回给前端的配置（api_key 只显示是否已设置与掩码）。"""
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "has_key": bool(self.api_key),
            "masked_key": ("****" + self.api_key[-4:]) if len(self.api_key) > 4 else "",
            "ready": self.ready,
            "use_system_proxy": self.use_system_proxy,
            "system_prompt": self.system_prompt,
            "default_system_prompt": DEFAULT_SYSTEM_PROMPT,
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
        return AIConfig.from_dict(data)

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


class LLMClient:
    """OpenAI 兼容 /chat/completions 客户端（同步，供线程池调用）。"""

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg

    def chat(self, messages: list[dict], *, max_tokens: Optional[int] = None, timeout_s: Optional[float] = None) -> dict:
        """返回 {text, model, elapsed_ms, usage}；失败抛 LLMError。"""
        if not self.cfg.ready:
            raise LLMError("AI 未配置：请在设置页填写 base_url / model（以及 api_key）")
        candidates = _chat_candidates(self.cfg.base_url)
        if not candidates:
            raise LLMError("AI base_url 为空")
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = "Bearer " + self.cfg.api_key
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
            for base in candidates:
                url = base + "/chat/completions"
                attempted.append(url)
                try:
                    resp = client.post(url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    raise LLMError("无法连接模型服务 " + url + ": " + str(exc)) from exc
                if resp.status_code in (404, 405) and base is not candidates[-1]:
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

    def ping(self) -> dict:
        """连通性测试：最小对话一次。"""
        return self.chat([{"role": "user", "content": "你好，请回复 ok"}], max_tokens=8, timeout_s=20)