"""检索评测指标：DCG/nDCG 与理想排序归一化。"""
from __future__ import annotations

import math


def dcg(relevances: list[float], k: int = 10) -> float:
    """折损累计增益；relevances 按实际排序，允许 0/1 或分级相关度。"""
    return sum(float(r) / math.log2(i + 2) for i, r in enumerate(relevances[:k]))


def ndcg(relevances: list[float], k: int = 10) -> float:
    """归一化 DCG：分母是同一组相关度的理想排序 DCG@k。"""
    actual = dcg(relevances, k)
    ideal = dcg(sorted(relevances, reverse=True), k)
    return actual / ideal if ideal > 0 else 0.0


def binary_relevances(doc_ids: list[int], expected: set[int], k: int = 10) -> list[int]:
    return [1 if int(doc_id) in expected else 0 for doc_id in doc_ids[:k]]
