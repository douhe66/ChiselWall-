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

    def search_ranked(self, query: str, topk: int = 8) -> list[dict]:
        """经典搜索入口：本地召回 + 可选重排，保持旧 /api/search 行为。"""
        q = (query or "").strip()
        if not q:
            return []
        hits = [h.to_dict() for h in self.agent.search_local_expanded(q, topk)]
        return self.agent.rerank_hits(q, hits)[: max(1, int(topk))]

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
                session.set_coverage(view.get("expected") or 0, view.get("responded") or 0)
                if view.get("status") == "error":
                    self.last_error = str(view.get("error") or "relay query failed")
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("research search relay failed: %s", exc)
        if not hits:
            local = await asyncio.to_thread(self.agent.searcher.search, query, topk)
            hits = [h.to_dict() for h in local]
        # AI 子查询不再二次 rerank：首轮聚合已按配置处理，rerank 不是 AI 的硬依赖。
        added = session.add_hits(hits, node_fallback=self.nick)
        await self.hydrate(session)
        log.info("retrieval search session=%s q=%s hits=%s new=%s",
                 session.id, len(query), len(hits), len(added))
        return added

    async def hydrate(self, session: EvidenceSession) -> None:
        for ev in session.evidence.values():
            if ev.node == self.nick and ev.doc_id:
                try:
                    doc = await asyncio.to_thread(self.agent.db.get_doc_by_id, int(ev.doc_id))
                    if doc is not None:
                        ev.content_hash = str(doc.hash or "")
                        ev.mtime = float(doc.mtime or 0.0)
                        ev.size = int(doc.size or 0)
                        if ev.total_chars <= 0:
                            ev.total_chars = await asyncio.to_thread(
                                self.agent.db.doc_text_chars, int(ev.doc_id)
                            )
                except Exception:
                    pass

    async def search_plan(self, session: EvidenceSession, plan, *, topk: int = 8) -> list[Evidence]:
        """Plan-first：用预生成 QueryPlan 做首轮检索，不再二次调用 QueryPlan。"""
        original = str(getattr(plan, "original", "") or (plan.get("original") if isinstance(plan, dict) else "") or "").strip()
        expression = str(getattr(plan, "expression", "") or (plan.get("expression") if isinstance(plan, dict) else "") or "").strip()
        if not original:
            return []
        session.add_query(original)
        session.plan_text = expression or original
        hits: list[dict] = []
        bus = getattr(self.agent, "bus", None)
        if bus is not None and getattr(bus.cfg, "enabled", False) and bus.connected:
            try:
                result = await bus.ask(
                    original, session.groups or None, use_plan=False,
                    research_id=session.id, plan_override=plan,
                )
                qid = str(result.get("qid") or "")
                deadline = time.monotonic() + max(6.0, self.agent.config.ask_deadline_ms / 1000.0 + 2.0)
                view = {}
                while time.monotonic() < deadline:
                    view = bus.view(qid) or {}
                    if view.get("status") in ("done", "error"):
                        break
                    await asyncio.sleep(0.15)
                hits = list(view.get("aggregated") or [])
                session.set_coverage(view.get("expected") or 0, view.get("responded") or 0)
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("plan search relay failed: %s", exc)
        else:
            from ai.query_plan import evaluate_plan
            local = await asyncio.to_thread(evaluate_plan, self.agent.searcher, plan, topk)
            hits = [h.to_dict() for h in local]
        added = session.add_hits(hits, node_fallback=self.nick)
        await self.hydrate(session)
        log.info("retrieval plan search session=%s plan=%s hits=%s new=%s",
                 session.id, expression[:120], len(hits), len(added))
        return added

    async def fetch(self, session: EvidenceSession, evidence_id: str, max_chars: int = 24000,
                    mode: str = "preview", start: int = 0) -> Evidence:
        ev = session.get(evidence_id)
        if ev is None:
            raise LookupError("evidence 不存在: " + str(evidence_id))
        mode = str(mode).lower()
        if mode not in ("preview", "full", "around"):
            mode = "preview"
        start = max(0, int(start or 0))
        if mode == "around" and not start:
            start = max(0, int(ev.char_start or 0))
        max_chars = max(100, int(max_chars))
        if mode == "full" and ev.fetched and start == ev.read_start and ev.full_text:
            return ev
        if mode == "preview" and ev.previewed and start == ev.read_start and \
                len(ev.preview_text or "") >= max_chars:
            return ev
        local = ev.node == self.nick or ev.doc_id is None
        if local:
            doc = await asyncio.to_thread(self.agent.db.get_doc_by_id, int(ev.doc_id or 0))
            if doc is None or str(doc.status) != "indexed":
                raise LookupError("文档已删除或不可读")
            current_hash = str(doc.hash or "")
            if ev.content_hash and current_hash and current_hash != ev.content_hash:
                ev.version_changed = True
                log.info("retrieval fetch version changed evidence=%s old=%s new=%s",
                         evidence_id, ev.content_hash[:12], current_hash[:12])
            ev.content_hash = current_hash
            ev.mtime = float(doc.mtime or 0.0)
            ev.size = int(doc.size or 0)
            chunks = self.agent.db.get_chunk_texts(int(ev.doc_id or 0))
            full = "\n\n".join(chunks)
            ev.total_chars = len(full)
            take = full[start:start + max_chars]
        else:
            fetched = await self.agent.bus.fetch(
                ev.node, int(ev.doc_id), max_chars=max_chars, start=start,
                groups=session.groups or None, expected_hash=ev.content_hash,
            )
            take = str(fetched or "")[:max_chars]
        ev.read_start = start
        ev.read_end = start + len(take)
        ev.next_offset = ev.read_end
        ev.has_more = bool(ev.total_chars and ev.read_end < ev.total_chars)
        ev.truncated = ev.has_more or (not ev.total_chars and len(take) >= max_chars)
        if mode == "full":
            ev.full_text = take
            ev.fetched = bool(take)
            if start == 0:
                ev.segments = []
        else:
            ev.preview_text = take
            ev.previewed = bool(take)
        if take:
            ev.segments = [s for s in ev.segments
                           if not (int(s.get("start") or 0) == start
                                   and int(s.get("end") or 0) == ev.read_end)]
            ev.segments.append({"start": start, "end": ev.read_end, "mode": mode, "text": take})
        ev.read_chars = len(take)
        ev.read_mode = mode
        log.info("retrieval fetch session=%s evidence=%s mode=%s start=%s end=%s total=%s truncated=%s",
                 session.id, evidence_id, mode, ev.read_start, ev.read_end, ev.total_chars, ev.truncated)
        return ev
