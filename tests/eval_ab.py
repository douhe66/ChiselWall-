"""P2/P1 2x2 对比评估：rewrite 开/关 × rerank 开/关。

用法：
  python tests/eval_ab.py [--golden tests/fixtures/golden/queries_v2.json] [--db ~/.chiselwall/chiselwall.db]

只读快照后评估，不写原库。
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.embedding import LocalOnnxEmbeddings
from ai.llm import AIConfigStore
from ai.query_rewrite import QueryExpandConfig, QueryRewriter
from ai.rerank import LocalCrossEncoderReranker, RerankConfig
from ai.vector_index import VectorIndex
from ai.vectorstore import VectorStore
from indexer.db import IndexDB
from searcher.search import SearchHit, Searcher

ROOT = Path(__file__).resolve().parents[1]
EMB_DIR = ROOT / "models" / "bge-small-zh-v1.5"
RERANK_DIR = ROOT / "models" / "rerank" / "bge-reranker-base-int8"


def snapshot_db(src: Path, dst: Path) -> None:
    con = sqlite3.connect("file:%s?mode=ro" % str(src), uri=True)
    out = sqlite3.connect(str(dst))
    try:
        con.backup(out)
    finally:
        out.close()
        con.close()


def rrf_merge(lists: list[list[SearchHit]], topk: int) -> list[SearchHit]:
    scores: dict[int, float] = {}
    best: dict[int, SearchHit] = {}
    for hits in lists:
        for rank, hit in enumerate(hits):
            key = int(hit.doc_id)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank + 1)
            best.setdefault(key, hit)
    out = []
    for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:topk]:
        hit = best[key]
        hit.score = float(score)
        out.append(hit)
    return out


def rerank_hits(reranker, hits: list[SearchHit], query: str, top_k: int = 10) -> list[SearchHit]:
    strong = [h for h in hits if getattr(h, "tier", "strong") != "weak"]
    weak = [h for h in hits if getattr(h, "tier", "strong") == "weak"]
    if len(strong) < 2:
        return hits
    n = min(len(strong), 12)
    docs = [(h.title or "") + "\n" + (h.snippet_text or "") for h in strong[:n]]
    scores = reranker.rerank(query, docs)
    ranked = sorted(zip(strong[:n], scores), key=lambda pair: pair[1], reverse=True)
    out = []
    for hit, score in ranked:
        hit.score = float(score)
        out.append(hit)
    out.extend(strong[n:])
    out.extend(weak)
    return out[:top_k]


def dcg(rels: list[int], k: int = 10) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels[:k]))


def measure(hits: list[SearchHit], expected: set[int], k: int = 10) -> dict:
    ids = [h.doc_id for h in hits[:k]]
    rank = next((i + 1 for i, doc_id in enumerate(ids) if doc_id in expected), 0)
    rels = [1 if doc_id in expected else 0 for doc_id in ids]
    return {
        "rank": rank,
        "recall5": 1.0 if 0 < rank <= 5 else 0.0,
        "mrr": (1.0 / rank) if rank else 0.0,
        "ndcg10": dcg(rels) / (dcg([1]) or 1.0),
        "weak": sum(1 for h in hits if getattr(h, "tier", "strong") == "weak"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path.home() / ".chiselwall" / "chiselwall.db"))
    parser.add_argument("--golden", default=str(Path(__file__).parent / "fixtures" / "golden" / "queries_v2.json"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    if args.limit:
        golden = golden[: args.limit]

    tmp = Path(tempfile.mkdtemp(prefix="cw-ab-"))
    try:
        snap = tmp / "snap.db"
        snapshot_db(Path(args.db), snap)
        db = IndexDB(snap)
        emb = LocalOnnxEmbeddings(str(EMB_DIR))
        store = VectorStore(db.path, dim=512)
        vi = VectorIndex(db, store, emb)
        searcher = Searcher(db, vector_index=vi)

        ai_cfg = AIConfigStore(Path.home() / ".chiselwall").load()
        rewriter = QueryRewriter(QueryExpandConfig(enabled=True, max_queries=3, timeout_s=8), ai_cfg)
        reranker = LocalCrossEncoderReranker(
            RerankConfig(
                enabled=True, provider="local", model_dir=str(RERANK_DIR),
                max_candidates=12, top_k=10, idle_unload_s=0, cache_size=500,
            )
        )

        # 期望文档（按标题 LIKE 解析）
        items = []
        for item in golden:
            expected: set[int] = set()
            for pat in item.get("expect_title_like") or []:
                rows = db.query("SELECT doc_id FROM docs WHERE status='indexed' AND title LIKE ?", (pat,))
                expected.update(int(r["doc_id"]) for r in rows)
            if not expected:
                print("SKIP（期望为空）:", item["query"], item.get("expect_title_like"))
                continue
            items.append((item["query"], expected))

        # 预热（模型加载不计入逐条均值，单独记录）
        t0 = time.time()
        searcher.search("预热", 3)
        emb_ms = int((time.time() - t0) * 1000)

        arms = {"base": [], "rewrite": [], "rerank": [], "both": []}
        times = {k: [] for k in arms}
        warm = {}
        for idx, (q, expected) in enumerate(items):
            # base
            t = time.time(); base = searcher.search(q, 10); times["base"].append((time.time() - t) * 1000)
            arms["base"].append(measure(base, expected))

            # rewrite
            t = time.time()
            variants = rewriter.rewrite(q)
            lists = [base] + [searcher.search(v, 10) for v in variants] if variants else [base]
            rw = rrf_merge(lists, 10)
            times["rewrite"].append((time.time() - t) * 1000)
            arms["rewrite"].append(measure(rw, expected))

            # rerank
            t = time.time(); rr = rerank_hits(reranker, base, q); times["rerank"].append((time.time() - t) * 1000)
            arms["rerank"].append(measure(rr, expected))

            # both
            t = time.time(); both = rerank_hits(reranker, rw, q); times["both"].append((time.time() - t) * 1000)
            arms["both"].append(measure(both, expected))

        def summarize(key: str) -> dict:
            rows = arms[key]
            n = max(1, len(rows))
            ts = sorted(times[key])
            return {
                "recall5": sum(r["recall5"] for r in rows) / n,
                "ndcg10": sum(r["ndcg10"] for r in rows) / n,
                "mrr": sum(r["mrr"] for r in rows) / n,
                "weak": sum(r["weak"] for r in rows) / n,
                "avg_ms": sum(times[key]) / n,
                "p50_ms": ts[len(ts) // 2] if ts else 0,
                "p95_ms": ts[min(len(ts) - 1, int(len(ts) * 0.95))] if ts else 0,
            }

        print("queries:", len(items), "emb_warmup_ms:", emb_ms)
        for key in arms:
            s = summarize(key)
            print("%-8s recall@5=%.3f nDCG@10=%.3f MRR=%.3f weak=%.2f avg=%.0fms p50=%.0fms p95=%.0fms" % (
                key, s["recall5"], s["ndcg10"], s["mrr"], s["weak"], s["avg_ms"], s["p50_ms"], s["p95_ms"],
            ))
        print("--- per query (base / rewrite / rerank / both ranks) ---")
        for (q, _), a, b, c, d in zip(items, arms["base"], arms["rewrite"], arms["rerank"], arms["both"]):
            print("%-28s %2s %2s %2s %2s" % (q[:28], a["rank"] or "-", b["rank"] or "-", c["rank"] or "-", d["rank"] or "-"))

        result = {
            "queries": [q for q, _ in items],
            "summary": {k: summarize(k) for k in arms},
            "per_query": [
                {"query": q, "base": a["rank"], "rewrite": b["rank"], "rerank": c["rank"], "both": d["rank"]}
                for (q, _), a, b, c, d in zip(items, arms["base"], arms["rewrite"], arms["rerank"], arms["both"])
            ],
        }
        out = ROOT / "tests" / "tmp" / "ab_result.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", out)
        db.close()
        store.close()
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
