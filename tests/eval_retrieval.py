"""检索质量评估：对真实/测试库跑 golden set，输出 recall@5 / nDCG@10 / 弱相关占比。

用法：python tests/eval_retrieval.py --db <索引库路径> [--golden tests/fixtures/golden/queries.json]
说明：只读；不启动服务。用于改检索前后对比，避免凭感觉调参。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_common import expected_doc_ids
from indexer.db import IndexDB
from retrieval.metrics import binary_relevances, ndcg
from searcher.search import Searcher


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
        expected = expected_doc_ids(db, item)
        if not expected:
            print("SKIP（期望为空）:", item["query"], item.get("expect"), item.get("expect_title_like"))
            continue
        ids = [int(h["doc_id"]) for h in strong[:10]]
        rank = next((i + 1 for i, doc_id in enumerate(ids) if doc_id in expected), 0)
        r5 = 1 if 0 < rank <= 5 else 0
        rels = binary_relevances(ids, expected, 10)
        score_ndcg = ndcg(rels, 10)
        total_r5 += r5
        total_ndcg += score_ndcg
        total_weak += len(weak)
        print("%-16s rank=%-2s recall@5=%s nDCG=%.2f weak=%d  top=%s" % (
            item["query"], rank or "-", r5, score_ndcg, len(weak),
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