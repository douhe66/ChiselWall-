"""QueryPlan 软 AND 检索：OR 召回，覆盖度排序，不硬过滤。"""
from __future__ import annotations

from typing import Any, Optional

from searcher.search import SearchHit


def _as_plan(plan: Any) -> Optional[dict]:
    if plan is None:
        return None
    if hasattr(plan, "to_dict"):
        plan = plan.to_dict()
    if not isinstance(plan, dict):
        return None
    clauses = []
    for item in plan.get("clauses") or []:
        if isinstance(item, dict):
            item = item.get("terms") or []
        if isinstance(item, list):
            terms = [str(t).strip() for t in item if str(t).strip()]
            if terms:
                clauses.append(terms[:4])
    paraphrases = [str(p).strip() for p in (plan.get("paraphrases") or []) if str(p).strip()]
    if not clauses and not paraphrases:
        return None
    return {"clauses": clauses[:4], "paraphrases": paraphrases[:2]}


def evaluate_plan(searcher: Any, plan: Any, topk: int = 8) -> list[SearchHit]:
    """软 AND：所有词只用于 OR 召回和覆盖度排序，绝不用交集过滤。"""
    spec = _as_plan(plan)
    if spec is None:
        return []
    original = str(getattr(plan, "original", "") or (plan.get("original") if isinstance(plan, dict) else "") or "").strip()
    if not original:
        return []
    try:
        base = list(searcher.search(original, topk))
    except Exception:
        base = []
    per = max(topk * 2, 8)
    clause_maps: list[dict[int, SearchHit]] = []
    for terms in spec["clauses"]:
        best: dict[int, SearchHit] = {}
        for term in terms:
            try:
                hits = searcher.search(term, per)
            except Exception:
                continue
            for hit in hits:
                old = best.get(int(hit.doc_id))
                if old is None or float(hit.score) > float(old.score):
                    best[int(hit.doc_id)] = hit
        clause_maps.append(best)
    paraphrase_hits: list[SearchHit] = []
    for paraphrase in spec["paraphrases"]:
        try:
            paraphrase_hits.extend(searcher.search(paraphrase, per))
        except Exception:
            continue
    candidates: dict[int, SearchHit] = {}
    for hit in base + paraphrase_hits:
        old = candidates.get(int(hit.doc_id))
        if old is None or float(hit.score) > float(old.score):
            candidates[int(hit.doc_id)] = hit
    for mapping in clause_maps:
        for hit in mapping.values():
            old = candidates.get(int(hit.doc_id))
            if old is None or float(hit.score) > float(old.score):
                candidates[int(hit.doc_id)] = hit
    if not candidates:
        return base[:topk]
    base_rank = {int(h.doc_id): i for i, h in enumerate(base)}
    total = len(clause_maps)
    ranked = []
    for doc_id, hit in candidates.items():
        coverage = sum(1 for mapping in clause_maps if doc_id in mapping)
        score = float(hit.score)
        ranked.append((-coverage, base_rank.get(doc_id, 10_000), -score, doc_id, hit, coverage, total))
    ranked.sort()
    out: list[SearchHit] = []
    for _nc, _br, _ns, _id, hit, coverage, clauses in ranked[:topk]:
        hit.route = "plan"
        hit.plan_coverage = coverage
        hit.plan_clause_total = clauses
        out.append(hit)
    return out
