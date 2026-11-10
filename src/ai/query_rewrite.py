"""P2 查询改写：把自然语言问题扩成 2–3 个检索查询（LLM，可选、默认关）。

- 只影响「提问端」的召回：本地搜索与随手问的本机回答；不改变被搜索端；
- 失败/超时/未配置 → 返回空列表，原查询照常执行；
- 结果缓存（query -> variants，LRU + TTL），减少 LLM 调用。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from ai.llm import AIConfig, LLMClient, LLMError

log = logging.getLogger("chiselwall.query")

DEFAULT_QUERY_PROMPT = (
    "你是检索计划生成器。请把用户问题拆成 1-4 个“概念组”，输出严格 JSON（不要代码块、不要解释）：\n"
    "{\"clauses\":[{\"terms\":[\"同义词1\",\"同义词2\"]}],\"paraphrases\":[\"完整检索问句\"]}\n"
    "规则：\n"
    "1. 同一概念的同义词、缩写、中英文变体放进同一组；组内是 OR。\n"
    "2. 不同必要条件分成不同组；组间是 AND 的“覆盖度”目标，不是硬过滤。\n"
    "3. 去掉“怎么/如何/一下/请问/是什么”等问句词，不要输出“使用/用法/示例/教程/说明/介绍”等泛词，不要添加问题中不存在的新事实。\n"
    "4. 最多 4 组，每组最多 4 个词；paraphrases 最多 2 条完整问句，用于向量召回。\n"
    "5. 只输出 JSON。\n"
)
SYSTEM_PROMPT = DEFAULT_QUERY_PROMPT  # 兼容旧引用

PLAN_GENERIC_TERMS = frozenset({
    "使用", "用法", "示例", "教程", "说明", "介绍", "方法", "问题", "相关",
    "怎么", "如何", "什么", "一下", "请问", "帮我", "看看",
})


def _plan_term_ok(term: str, seen: set[str]) -> bool:
    key = term.casefold()
    if len(term) < 2 or len(term) > 40 or key in seen or term in PLAN_GENERIC_TERMS:
        return False
    return True

# v0.9.7 之前保存的默认提示词（逐行检索式）；加载时自动升级到 JSON 计划提示词。
LEGACY_QUERY_PROMPT = "你是搜索查询改写器。请从用户的问题中提取 1-3 个检索式，每个检索式只保留与问题相关的关键词；不要问句、不要解释、不要编号，一行一个。保留专有名词和英文术语原样（如 fcntl、nginx），不要添加“使用方法/用法/介绍/相关/测试”等泛词，不要引入问题中不存在的新事实。"


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
        cfg = QueryExpandConfig.from_dict(data) if isinstance(data, dict) else QueryExpandConfig()
        if (cfg.system_prompt or "").strip() == LEGACY_QUERY_PROMPT.strip():
            cfg.system_prompt = DEFAULT_QUERY_PROMPT
        return cfg

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


@dataclass
class QueryPlan:
    """发送前的结构化检索计划：clauses 组内 OR，组间软 AND 覆盖度。"""
    original: str
    clauses: list[list[str]] = field(default_factory=list)
    paraphrases: list[str] = field(default_factory=list)
    source: str = "llm"

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "original": self.original,
            "clauses": [{"terms": list(g)} for g in self.clauses],
            "paraphrases": list(self.paraphrases),
            "source": self.source,
            "expression": self.expression,
        }

    @property
    def expression(self) -> str:
        parts = []
        for group in self.clauses:
            if group:
                parts.append("(" + " OR ".join(group) + ")")
        return " AND ".join(parts)

    @classmethod
    def from_dict(cls, data: object) -> Optional["QueryPlan"]:
        if not isinstance(data, dict):
            return None
        original = str(data.get("original") or "").strip()
        clauses: list[list[str]] = []
        for raw in data.get("clauses") or []:
            if isinstance(raw, dict):
                raw = raw.get("terms") or raw.get("terms_or") or []
            if not isinstance(raw, list):
                continue
            group = []
            seen_terms: set[str] = set()
            for term in raw:
                t = str(term or "").strip()
                if _plan_term_ok(t, seen_terms):
                    seen_terms.add(t.casefold())
                    group.append(t)
            if group:
                clauses.append(group[:4])
        clauses = clauses[:4]
        paraphrases = []
        for raw in data.get("paraphrases") or []:
            p = str(raw or "").strip()
            if 3 <= len(p) <= 80 and p not in paraphrases:
                paraphrases.append(p)
        paraphrases = paraphrases[:2]
        if not clauses:
            return None
        return cls(original=original, clauses=clauses, paraphrases=paraphrases,
                   source=str(data.get("source") or "llm"))


def parse_plan(text: str, original: str, max_groups: int = 4, max_terms: int = 4,
               max_paraphrases: int = 2) -> Optional[QueryPlan]:
    """解析模型 JSON；容忍代码块/前后说明；不合法返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if "```" in raw:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    clauses: list[list[str]] = []
    for item in data.get("clauses") or []:
        if isinstance(item, dict):
            item = item.get("terms") or item.get("terms_or") or []
        if not isinstance(item, list):
            continue
        group: list[str] = []
        seen_terms: set[str] = set()
        for term in item:
            t = str(term or "").strip().strip("。；;，,")
            if not _plan_term_ok(t, seen_terms):
                continue
            seen_terms.add(t.casefold())
            group.append(t)
            if len(group) >= max_terms:
                break
        if group:
            clauses.append(group)
        if len(clauses) >= max_groups:
            break
    paraphrases: list[str] = []
    for item in data.get("paraphrases") or []:
        p = str(item or "").strip()
        if 3 <= len(p) <= 80 and p != original.strip() and p not in paraphrases:
            paraphrases.append(p)
        if len(paraphrases) >= max_paraphrases:
            break
    if not clauses:
        return None
    return QueryPlan(original=original.strip(), clauses=clauses, paraphrases=paraphrases, source="llm")

class QueryRewriter:
    def __init__(self, cfg: QueryExpandConfig, ai_config: AIConfig) -> None:
        self.cfg = cfg
        self.ai_config = ai_config
        self._cache: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()
        self._plan_cache: OrderedDict[str, tuple[float, Optional[QueryPlan]]] = OrderedDict()
        self._lock = threading.Lock()
        self.last_raw = ""

    @property
    def ready(self) -> bool:
        return bool(self.cfg.enabled and self.ai_config.ready)

    def plan(self, question: str) -> Optional[QueryPlan]:
        """发送前生成结构化计划；未启用/未配置/失败返回 None（原问题兜底）。"""
        question = (question or "").strip()
        if not question or not self.ready:
            return None
        now = time.monotonic()
        with self._lock:
            hit = self._plan_cache.get(question)
            if hit is not None and now - hit[0] < 3600:
                self._plan_cache.move_to_end(question)
                return hit[1]
        try:
            client = LLMClient(self.ai_config)
            result = client.chat(
                [
                    {"role": "system", "content": self.cfg.system_prompt.strip() or DEFAULT_QUERY_PROMPT},
                    {"role": "user", "content": question},
                ],
                max_tokens=500,
                timeout_s=float(self.cfg.timeout_s),
            )
            self.last_raw = str(result.get("text") or "")[:2000]
            plan = parse_plan(
                self.last_raw, question,
                max_groups=max(1, min(4, int(self.cfg.max_queries or 4))),
            )
            if plan is None:
                log.warning("检索计划解析失败，回退原问题：%s", self.last_raw[:300])
        except (LLMError, ValueError) as exc:
            log.warning("检索计划生成失败：%s", exc)
            return None
        with self._lock:
            self._plan_cache[question] = (now, plan)
            self._plan_cache.move_to_end(question)
            while len(self._plan_cache) > 128:
                self._plan_cache.popitem(last=False)
        return plan

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
