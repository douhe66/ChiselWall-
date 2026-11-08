"""P2 查询改写：把自然语言问题扩成 2–3 个检索查询（LLM，可选、默认关）。

- 只影响「提问端」的召回：本地搜索与随手问的本机回答；不改变被搜索端；
- 失败/超时/未配置 → 返回空列表，原查询照常执行；
- 结果缓存（query -> variants，LRU + TTL），减少 LLM 调用。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from ai.llm import AIConfig, LLMClient, LLMError

log = logging.getLogger("chiselwall.query")

DEFAULT_QUERY_PROMPT = (
    "你是搜索查询改写器。请从用户的问题中提取 1-3 个检索式，"
    "每个检索式只保留与问题相关的关键词；不要问句、不要解释、不要编号，一行一个。"
    "保留专有名词和英文术语原样（如 fcntl、nginx），"
    "不要添加“使用方法/用法/介绍/相关/测试”等泛词，不要引入问题中不存在的新事实。"
)
SYSTEM_PROMPT = DEFAULT_QUERY_PROMPT  # 兼容旧引用


@dataclass
class QueryExpandConfig:
    enabled: bool = False
    max_queries: int = 3
    timeout_s: float = 4.0
    system_prompt: str = DEFAULT_QUERY_PROMPT

    @classmethod
    def from_dict(cls, data: dict) -> "QueryExpandConfig":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "max_queries": self.max_queries,
            "timeout_s": self.timeout_s,
            "system_prompt": self.system_prompt or DEFAULT_QUERY_PROMPT,
            "default_prompt": DEFAULT_QUERY_PROMPT,
        }


class QueryExpandStore:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "query.json"
        self.config_dir = config_dir

    def load(self) -> QueryExpandConfig:
        if not self.path.exists():
            return QueryExpandConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取 query.json 失败")
            return QueryExpandConfig()
        return QueryExpandConfig.from_dict(data) if isinstance(data, dict) else QueryExpandConfig()

    def save(self, cfg: QueryExpandConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def parse_rewrites(text: str, original: str, max_queries: int = 3) -> list[str]:
    """解析模型输出：兼容 JSON 数组 / 编号行 / 普通行；过滤重复、原文与过短。"""
    raw_text = (text or "").strip()
    candidates: list[str] = []
    if raw_text.startswith("[") or raw_text.startswith("{"):
        try:
            data = json.loads(raw_text)
            if isinstance(data, dict):
                data = data.get("queries") or data.get("variants") or []
            if isinstance(data, list):
                candidates = [str(x) for x in data]
        except ValueError:
            candidates = []
    if not candidates:
        candidates = raw_text.replace("\r", "\n").split("\n")
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        line = raw.strip().strip("*#- ").strip()
        line = line.lstrip("0123456789.、) ").strip()
        line = line.strip("。；;，,")
        if not line or line == original.strip():
            continue
        if len(line) < 2 or len(line) > 40:
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= max_queries:
            break
    return out


class QueryRewriter:
    def __init__(self, cfg: QueryExpandConfig, ai_config: AIConfig) -> None:
        self.cfg = cfg
        self.ai_config = ai_config
        self._cache: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()
        self._lock = threading.Lock()
        self.last_raw = ""

    @property
    def ready(self) -> bool:
        return bool(self.cfg.enabled and self.ai_config.ready)

    def rewrite(self, question: str) -> list[str]:
        question = (question or "").strip()
        if not question or not self.ready:
            return []
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(question)
            if hit is not None and now - hit[0] < 3600:
                self._cache.move_to_end(question)
                return list(hit[1])
        try:
            client = LLMClient(self.ai_config)
            result = client.chat(
                [
                    {"role": "system", "content": self.cfg.system_prompt.strip() or DEFAULT_QUERY_PROMPT},
                    {"role": "user", "content": question},
                ],
                max_tokens=400,
                timeout_s=float(self.cfg.timeout_s),
            )
            self.last_raw = str(result.get("text") or "")[:500]
            variants = parse_rewrites(self.last_raw, question, self.cfg.max_queries)
        except (LLMError, ValueError) as exc:
            log.warning("查询改写失败：%s", exc)
            return []
        with self._lock:
            self._cache[question] = (now, list(variants))
            self._cache.move_to_end(question)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return variants
