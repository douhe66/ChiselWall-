"""RetrievalKernel：不依赖 AI 的搜索/取全文能力，供经典 UI 或 ResearchAgent 调用。"""
from __future__ import annotations

import asyncio
import time
import logging

from retrieval.session import Evidence, EvidenceSession

log = logging.getLogger("chiselwall.retrieval")


class RetrievalKernel:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.last_error = ""

    @property
    def nick(self) -> str:
        return self.agent.config.relay_nick or "我"

    async def search(self, session: EvidenceSession, query: str, *, topk: int = 8) -> list[Evidence]:
        query = (query or "").strip()
        if not query:
            return []
        session.add_query(query)
        hits: list[dict] = []
        # 配置 relay 时走一次完整 ask（本地+远端）；否则只搜本地。
        bus = getattr(self.agent, "bus", None)
        if bus is not None and getattr(bus.cfg, "enabled", False) and bus.connected:
            try:
                result = await bus.ask(query, session.groups or None, use_plan=False, research_id=session.id)
                qid = str(result.get("qid") or "")
                deadline = time.monotonic() + max(6.0, self.agent.config.ask_deadline_ms / 1000.0 + 2.0)
                view = {}
                while time.monotonic() < deadline:
                    view = bus.view(qid) or {}
                    if view.get("status") in ("done", "error"):
                        break
                    await asyncio.sleep(0.15)
                hits = list(view.get("aggregated") or [])
                if view.get("status") == "error":
                    self.last_error = str(view.get("error") or "relay query failed")
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("research search relay failed: %s", exc)
        if not hits:
            local = await asyncio.to_thread(self.agent.searcher.search, query, topk)
            hits = [h.to_dict() for h in local]
        added = session.add_hits(hits, node_fallback=self.nick)
        log.info("retrieval search session=%s q=%s hits=%s new=%s",
                 session.id, len(query), len(hits), len(added))
        return added

    async def fetch(self, session: EvidenceSession, evidence_id: str, max_chars: int = 24000) -> Evidence:
        ev = session.get(evidence_id)
        if ev is None:
            raise LookupError("evidence 不存在: " + str(evidence_id))
        max_chars = max(1000, int(max_chars))
        if ev.fetched and ev.full_text:
            return ev
        text = ""
        if ev.node == self.nick or ev.doc_id is None:
            chunks = self.agent.db.get_chunk_texts(int(ev.doc_id or 0))
            text = "\n\n".join(chunks)
        else:
            text = await self.agent.bus.fetch(ev.node, int(ev.doc_id), max_chars=max_chars)
        ev.full_text = str(text or "")[:max_chars]
        ev.truncated = len(text or "") > max_chars
        ev.fetched = bool(ev.full_text)
        log.info("retrieval fetch session=%s evidence=%s chars=%s truncated=%s",
                 session.id, evidence_id, len(ev.full_text), ev.truncated)
        return ev
