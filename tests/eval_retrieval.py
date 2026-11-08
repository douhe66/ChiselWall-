"""检索质量评估：对真实/测试库跑 golden set，输出 recall@5 / nDCG@10 / 弱相关占比。

用法：python tests/eval_retrieval.py --db <索引库路径> [--golden tests/fixtures/golden/queries.json]
说明：只读；不启动服务。用于改检索前后对比，避免凭感觉调参。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB
from searcher.search import Searcher


def dcg(rels: list[int], k: int = 10) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels[:k]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--golden", default=str(Path(__file__).parent / "fixtures" / "golden" / "queries.json"))
    parser.add_argument("--topk", type=int, default=10)
    args = parser.parse_args()
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    db = IndexDB(Path(args.db))
    searcher = Searcher(db)
    total_r5 = 0
    total_ndcg = 0.0
    total_weak = 0
    for item in golden:
        hits = [h.to_dict() for h in searcher.search(item["query"], args.topk)]
        strong = [h for h in hits if h.get("tier", "strong") != "weak"]
        weak = [h for h in hits if h.get("tier", "strong") == "weak"]
        titles = [h["title"] for h in strong]
        expect = item.get("expect") or []
        rank = next((i + 1 for i, t in enumerate(titles) if t in expect), 0)
        r5 = 1 if 0 < rank <= 5 else 0
        rels = [1 if t in expect else 0 for t in titles[:10]]
        ndcg = dcg(rels) / (dcg([1]) or 1.0)
        total_r5 += r5
        total_ndcg += ndcg
        total_weak += len(weak)
        print("%-16s rank=%-2s recall@5=%s weak=%d  top=%s" % (
            item["query"], rank or "-", r5, len(weak),
            " | ".join(t[:18] for t in titles[:4]),
        ))
    n = max(1, len(golden))
    print("---")
    print("recall@5 = %.2f  nDCG@10 = %.3f  avg_weak = %.2f  (%d queries)" % (
        total_r5 / n, total_ndcg / n, total_weak / n, len(golden),
    ))
    db.close()
    return 0 if total_r5 / n >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())