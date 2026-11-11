"""检索证据会话：给搜索/取全文/引用提供稳定 ID，供 AI 或普通 UI 使用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid


@dataclass
class Evidence:
    evidence_id: str
    node: str
    doc_id: Optional[int]
    title: str
    rel_path: str
    format: str = ""
    tier: str = "strong"
    score: float = 0.0
    snippet: str = ""
    match_fields: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    full_text: str = ""
    truncated: bool = False
    fetched: bool = False
    raw: dict = field(default_factory=dict)

    def card(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "title": self.title,
            "rel_path": self.rel_path,
            "node": self.node,
            "format": self.format,
            "tier": self.tier,
            "score": self.score,
            "match_fields": list(self.match_fields),
            "snippet": self.snippet,
            "fetched": self.fetched,
            "chars": len(self.full_text or self.snippet or ""),
        }


class EvidenceSession:
    """一次研究/一次问答的证据账本；不持久化全文，只在内存中。"""

    def __init__(self, question: str, groups: Optional[list[str]] = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.question = question
        self.groups = list(groups or [])
        self.evidence: dict[str, Evidence] = {}
        self.queries: list[str] = []
        self._counter = 0
        self._keys: dict[tuple, str] = {}

    def add_hits(self, hits: list[dict], *, node_fallback: str = "?") -> list[Evidence]:
        added: list[Evidence] = []
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            sources = list(hit.get("sources") or [])
            node = str((sources[0] or {}).get("node") if sources else (hit.get("node") or node_fallback))
            doc_id = hit.get("doc_id")
            if doc_id is None and sources:
                doc_id = (sources[0] or {}).get("doc_id")
            key = (node, doc_id if doc_id is not None else (hit.get("title"), hit.get("rel_path")))
            if key in self._keys:
                ev = self.evidence[self._keys[key]]
                # 同一文档：优先保留有正文/更强 tier 的版本
                if (len(hit.get("snippet_text") or ""), hit.get("tier") == "strong") > (
                    len(ev.snippet or ""), ev.tier == "strong"
                ):
                    ev.snippet = str(hit.get("snippet_text") or ev.snippet)
                    ev.match_fields = list(hit.get("match_fields") or ev.match_fields)
                    ev.raw = hit
                continue
            self._counter += 1
            eid = "e%d" % self._counter
            ev = Evidence(
                evidence_id=eid,
                node=node,
                doc_id=int(doc_id) if doc_id is not None else None,
                title=str(hit.get("title") or ""),
                rel_path=str(hit.get("rel_path") or ""),
                format=str(hit.get("format") or ""),
                tier=str(hit.get("tier") or "strong"),
                score=float(hit.get("score") or 0.0),
                snippet=str(hit.get("snippet_text") or hit.get("snippet_html") or ""),
                match_fields=list(hit.get("match_fields") or []),
                sources=sources,
                raw=hit,
            )
            self.evidence[eid] = ev
            self._keys[key] = eid
            added.append(ev)
        return added

    def add_evidence(self, ev: Evidence) -> None:
        self.evidence[ev.evidence_id] = ev

    def get(self, evidence_id: str) -> Optional[Evidence]:
        return self.evidence.get((evidence_id or "").strip())

    def cards(self) -> list[dict]:
        return [e.card() for e in self.evidence.values()]

    def fetched_ids(self) -> list[str]:
        return [e.evidence_id for e in self.evidence.values() if e.fetched]

    def add_query(self, query: str) -> bool:
        q = (query or "").strip()
        if not q or q in self.queries:
            return False
        self.queries.append(q)
        return True
