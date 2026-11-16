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
    preview_text: str = ""
    total_chars: int = 0
    read_chars: int = 0
    read_mode: str = ""
    chunk_seq: int = 0
    char_start: int = 0
    read_start: int = 0
    read_end: int = 0
    next_offset: int = 0
    has_more: bool = False
    version_changed: bool = False
    content_hash: str = ""
    mtime: float = 0.0
    size: int = 0
    truncated: bool = False
    fetched: bool = False
    previewed: bool = False
    segments: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def body(self, limit: int = 0) -> str:
        if self.segments:
            parts = []
            used = 0
            for seg in sorted(self.segments, key=lambda x: int(x.get("start") or 0)):
                text = str(seg.get("text") or "")
                if not text:
                    continue
                head = "[%s:%s]\n" % (seg.get("start"), seg.get("end"))
                take = text
                if limit > 0:
                    left = limit - used - len(head)
                    if left <= 0:
                        break
                    take = text[:left]
                parts.append(head + take)
                used += len(head) + len(take)
            if parts:
                return "\n".join(parts)
        text = self.full_text or self.preview_text or self.snippet or ""
        return text[:limit] if limit > 0 else text

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
            "previewed": self.previewed,
            "total_chars": int(self.total_chars or 0),
            "read_chars": int(self.read_chars or 0),
            "read_mode": self.read_mode,
            "chunk_seq": int(self.chunk_seq or 0),
            "char_start": int(self.char_start or 0),
            "read_start": int(self.read_start or 0),
            "read_end": int(self.read_end or 0),
            "next_offset": int(self.next_offset or 0),
            "has_more": self.has_more,
            "version_changed": self.version_changed,
            "content_hash": (self.content_hash or "")[:12],
            "read_ranges": [[int(s.get("start") or 0), int(s.get("end") or 0)]
                            for s in self.segments],
            "truncated": self.truncated,
            "chars": len(self.full_text or self.preview_text or self.snippet or ""),
        }


class EvidenceSession:
    """一次研究/一次问答的证据账本；不持久化全文，只在内存中。"""

    def __init__(self, question: str, groups: Optional[list[str]] = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.question = question
        self.groups = list(groups or [])
        self.evidence: dict[str, Evidence] = {}
        self.queries: list[str] = []
        self.coverage: dict = {"expected": 0, "responded": 0, "partial": False}
        self.plan_text: str = ""
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
                total_chars=int(hit.get("total_chars") or 0),
                chunk_seq=int(hit.get("chunk_seq") or 0),
                char_start=int(hit.get("char_start") or 0),
                content_hash=str(hit.get("content_hash") or ""),
                mtime=float(hit.get("mtime") or 0.0),
                size=int(hit.get("size") or 0),
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

    def set_coverage(self, expected: int, responded: int) -> None:
        expected = max(0, int(expected or 0))
        responded = max(0, int(responded or 0))
        if expected:
            self.coverage = {
                "expected": expected,
                "responded": responded,
                "partial": responded < expected,
            }

    def add_query(self, query: str) -> bool:
        q = (query or "").strip()
        if not q or q in self.queries:
            return False
        self.queries.append(q)
        return True
