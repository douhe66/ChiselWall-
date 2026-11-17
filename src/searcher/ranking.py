"""统一检索排序：所有阶段输出越大越好的 fused score + 可解释分量。

BM25 原始分保留在 raw_score（越小越好）；对外 score 统一为 RRF/fused 分（越大越好）。
"""
from __future__ import annotations

from typing import Any

RRF_K = 60


def rrf(rank: int, k: int = RRF_K, weight: float = 1.0) -> float:
    """0-based rank 的倒数排名融合分。"""
    return float(weight) / (float(k) + int(rank) + 1.0)


def components(hit: Any) -> dict:
    if isinstance(hit, dict):
        raw = hit.get("score_components")
    else:
        raw = getattr(hit, "score_components", None)
    return dict(raw) if isinstance(raw, dict) else {}


def set_score(hit: Any, score: float, stage: str, parts: dict | None = None) -> None:
    """写入统一的 final score、阶段和分量；不覆盖 raw_score。"""
    clean = {str(k): round(float(v), 6) for k, v in (parts or {}).items() if v is not None}
    value = round(float(score), 6)
    if isinstance(hit, dict):
        hit["score"] = value
        hit["score_stage"] = str(stage)
        hit["score_components"] = clean
    else:
        hit.score = value
        hit.score_stage = str(stage)
        hit.score_components = clean


def set_raw_score(hit: Any, raw: float) -> None:
    if isinstance(hit, dict):
        hit["raw_score"] = round(float(raw), 6)
    else:
        hit.raw_score = float(raw)
