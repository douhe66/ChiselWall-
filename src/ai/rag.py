"""提问者侧 RAG 综合（M5，设计 §10/§15.5）：聚合结果 -> 预算内上下文 -> 带引用回答。"""
from __future__ import annotations

import time
from typing import Optional

from ai.llm import DEFAULT_SYSTEM_PROMPT, AIConfig, LLMClient, LLMError

SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT


def select_local_window(chunks: list[str], seq: int, *, max_chars: int = 3000) -> str:
    """从文档分块中取命中块及相邻块（含代码块上下文），预算内尽量完整。"""
    if not chunks:
        return ""
    idx = min(max(int(seq), 0), len(chunks) - 1)
    parts = [chunks[i] for i in range(max(0, idx - 1), min(len(chunks), idx + 2))]
    text = "\n\n".join(parts)
    for i in range(idx + 2, min(len(chunks), idx + 4)):
        candidate = text + "\n\n" + chunks[i]
        if len(candidate) > max_chars:
            break
        text = candidate
    return text[:max_chars]


def build_context(
    aggregated: list[dict],
    *,
    max_chars: int = 24000,
    top_n: int = 24,
    snippet_chars: int = 320,
) -> tuple[str, list[dict]]:
    """把聚合命中拼成编号资料，并在字符预算内截断。返回 (context, citations)。"""
    parts: list[str] = []
    citations: list[dict] = []
    used = 0
    for hit in aggregated[:top_n]:
        title = str(hit.get("title") or "")[:80]
        # 远端节点会附带 ai_text（命中块及相邻块，约 1–4KB）；
        # 老版本/本地命中没有时回退到普通 snippet。
        snippet = str(hit.get("ai_text") or hit.get("snippet_text") or hit.get("snippet_html") or "")
        limit = snippet_chars
        if hit.get("ai_text"):
            limit = max_chars
        if hit.get("context_full"):
            limit = max(snippet_chars, min(16000, max_chars // 2))
        if hit.get("context_full") or hit.get("ai_text"):
            snippet = snippet[:limit]
        else:
            snippet = " ".join(snippet.split())[:limit]
        if not snippet:
            continue
        nodes = ",".join(hit.get("nodes") or []) or "?"
        rel = str(hit.get("rel_path") or "")
        weak = "；弱相关" if str(hit.get("tier") or "strong") == "weak" else ""
        block = "[%d] 《%s》（来源：%s；路径：%s%s）\n%s" % (len(citations) + 1, title, nodes, rel, weak, snippet)
        if used + len(block) > max_chars:
            if citations:
                break
            block = block[:max_chars]  # 单条超预算时硬截断，避免整个上下文超限
        parts.append(block)
        citations.append({
            "index": len(citations) + 1,
            "title": title,
            "nodes": hit.get("nodes") or [],
            "rel_path": rel,
            "format": hit.get("format") or "",
            "sources": hit.get("sources") or [],
        })
        used += len(block)
    return "\n\n".join(parts), citations


def build_messages(
    question: str,
    context: str,
    system_prompt: str = "",
    history: Optional[list[dict]] = None,
) -> list[dict]:
    """系统提示 + 最近历史（user/assistant 交替，逐条截断）+ 本轮问题与资料。"""
    messages = [{"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        q = str(turn.get("question") or turn.get("content") or "").strip()[:1500]
        a = str(turn.get("answer") or "").strip()[:1500]
        if q:
            messages.append({"role": "user", "content": q})
        if a:
            messages.append({"role": "assistant", "content": a})
    user = "问题：%s\n\n资料：\n%s" % (question.strip(), context) if context else "问题：%s" % question.strip()
    messages.append({"role": "user", "content": user})
    return messages


def stream_answer(
    question: str,
    aggregated: list[dict],
    cfg: AIConfig,
    *,
    history: Optional[list] = None,
):
    """流式综合：yield {type: start|delta|done|error, ...}。"""
    if not cfg.ready:
        yield {"type": "error", "error": "AI 未配置：请在设置页填写 base_url / model（以及 api_key）"}
        return
    context, citations = build_context(aggregated, max_chars=cfg.max_context_chars, top_n=cfg.top_n)
    strong_n = sum(1 for h in aggregated if str(h.get("tier") or "strong") != "weak")
    weak_n = sum(1 for h in aggregated if str(h.get("tier") or "strong") == "weak")
    started = time.monotonic()
    yield {
        "type": "start",
        "citations": citations,
        "context_chars": len(context),
        "strong_hits": strong_n,
        "weak_hits": weak_n,
        "weak_only": bool(weak_n and not strong_n),
        "model": cfg.model,
    }
    if not context:
        text = "没有找到可用于回答的资料。可以换一个问法，或确认共享笔记的同事在线。"
        yield {"type": "delta", "text": text}
        yield {
            "type": "done", "text": text, "citations": citations, "model": cfg.model,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "context_chars": 0, "strong_hits": strong_n, "weak_hits": weak_n,
            "weak_only": bool(weak_n and not strong_n),
        }
        return
    text_parts: list[str] = []
    client = LLMClient(cfg)
    messages = build_messages(question, context, cfg.system_prompt, history=history)
    try:
        for delta in client.chat_stream(messages):
            text_parts.append(delta)
            yield {"type": "delta", "text": delta}
    except LLMError as exc:
        # 某些 OpenAI 兼容端点不支持 stream：还没出字时回退到普通请求
        if text_parts:
            yield {"type": "error", "error": str(exc), "citations": citations}
            return
        try:
            result = client.chat(messages)
        except LLMError as fallback_exc:
            yield {"type": "error", "error": str(fallback_exc), "citations": citations}
            return
        text_parts.append(str(result.get("text") or ""))
        if text_parts[0]:
            yield {"type": "delta", "text": text_parts[0]}
    yield {
        "type": "done",
        "text": "".join(text_parts),
        "citations": citations,
        "model": cfg.model,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "context_chars": len(context),
        "strong_hits": strong_n,
        "weak_hits": weak_n,
        "weak_only": bool(weak_n and not strong_n),
    }


def answer_question(
    question: str,
    aggregated: list[dict],
    cfg: AIConfig,
    *,
    client: Optional[LLMClient] = None,
    history: Optional[list[dict]] = None,
) -> dict:
    """生成带引用的综合回答（线程池调用）。

    返回 {status, answer, citations, model, elapsed_ms, context_chars, error}。
    """
    if not cfg.ready:
        return {
            "status": "unconfigured",
            "answer": "",
            "citations": [],
            "model": "",
            "elapsed_ms": 0,
            "context_chars": 0,
            "error": "AI 未配置：请在设置页填写 base_url / model（以及 api_key）",
        }
    context, citations = build_context(
        aggregated, max_chars=cfg.max_context_chars, top_n=cfg.top_n
    )
    llm = client or LLMClient(cfg)
    try:
        result = llm.chat(
            build_messages(question, context, cfg.system_prompt, history=history),
            max_tokens=cfg.max_tokens,
        )
    except LLMError as exc:
        return {
            "status": "error",
            "answer": "",
            "citations": citations,
            "model": cfg.model,
            "elapsed_ms": 0,
            "context_chars": len(context),
            "error": str(exc),
        }
    return {
        "status": "ready",
        "answer": result["text"],
        "citations": citations,
        "model": result["model"],
        "elapsed_ms": result["elapsed_ms"],
        "context_chars": len(context),
        "error": "",
    }
