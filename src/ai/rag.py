"""提问者侧 RAG 综合（M5，设计 §10/§15.5）：聚合结果 -> 预算内上下文 -> 带引用回答。"""
from __future__ import annotations

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
    max_chars: int = 8000,
    top_n: int = 20,
    snippet_chars: int = 320,
) -> tuple[str, list[dict]]:
    """把聚合命中拼成编号资料，并在字符预算内截断。返回 (context, citations)。"""
    parts: list[str] = []
    citations: list[dict] = []
    used = 0
    for hit in aggregated[:top_n]:
        title = str(hit.get("title") or "")[:80]
        snippet = str(hit.get("snippet_text") or hit.get("snippet_html") or "")
        limit = snippet_chars
        if hit.get("context_full"):
            limit = max(snippet_chars, 5000)
        if hit.get("context_full"):
            snippet = snippet[:limit]
        else:
            snippet = " ".join(snippet.split())[:limit]
        if not snippet:
            continue
        nodes = ",".join(hit.get("nodes") or []) or "?"
        rel = str(hit.get("rel_path") or "")
        block = "[%d] 《%s》（来源：%s；路径：%s）\n%s" % (len(citations) + 1, title, nodes, rel, snippet)
        if used + len(block) > max_chars and citations:
            break
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
