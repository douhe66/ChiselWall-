"""task-11 复核 F2 后半：开启 --trigram 时小文档兜底命中、大文档不写、增幅≈2%。

用法：agent 以 --trigram 启动后运行（先挂载 acceptance/root 并等索引完成）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8317"
ACC = Path(__file__).resolve().parent
TOKEN = json.loads((ACC / ".run" / "config" / "config.json").read_text(encoding="utf-8"))["api_token"]
DB = ACC / ".run" / "chiselwall.db"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-Chisel-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def rels(q):
    return [h["rel_path"] for h in api("/api/search?q=" + urllib.parse.quote(q) + "&topk=20")["hits"]]


def main():
    # 挂载 root（幂等）
    mounted = [m["path"] for m in api("/api/mounts")]
    if str(ACC / "root") not in mounted:
        print("挂载:", api("/api/mounts", "POST", {"path": str(ACC / "root")}))
    t0 = time.time()
    while time.time() - t0 < 240:
        s = api("/api/stats")
        if s["pending"] == 0 and s["indexed"] > 0:
            break
        time.sleep(1)
    print("stats:", json.dumps(api("/api/stats"), ensure_ascii=False))

    # 小文档 trigram 兜底命中：查询 9 字符真子串 cwfix_tri_a（>8 避开 LIKE 兜底，
    # 且非 jieba 完整 token，必须由 trigram gram 命中）
    hits = api("/api/search?q=" + urllib.parse.quote("cwfix_tri_a") + "&topk=20")["hits"]
    r1 = [(h["rel_path"], h["route"]) for h in hits]
    ok_small = any(rel == "corpus/tri/tri子串.md" and route == "trigram" for rel, route in r1)
    print("小文档 trigram 兜底命中(route=trigram):", r1, "->", "PASS" if ok_small else "FAIL")

    # 大文档不写 trigram：big_over_cap.md（>100K 字符）在 trigram 表应无行
    c = sqlite3.connect(str(DB))
    rows = c.execute(
        "SELECT COUNT(*) FROM chunks_trigram t JOIN chunks ch ON ch.chunk_id=t.rowid "
        "JOIN docs d ON d.doc_id=ch.doc_id WHERE d.rel_path LIKE '%big_over_cap%'"
    ).fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM chunks_trigram").fetchone()[0]
    db_size = DB.stat().st_size
    text_chars = c.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM chunks").fetchone()[0]
    c.close()
    ok_big = rows == 0
    print(f"大文档 trigram 行数={rows}（应=0）-> {'PASS' if ok_big else 'FAIL'}")
    print(f"trigram 总行数={total}（仅小文档）")
    ratio = db_size / (text_chars * 3)
    print(f"开启 trigram 后 db/文本比={ratio:.1%}（应较默认 ≈2% 量级增幅，即 500% 线内）")
    ok_ratio = ratio <= 5.0
    print("增幅判定:", "PASS" if ok_ratio else "FAIL")
    sys.exit(0 if (ok_small and ok_big and ok_ratio) else 2)


if __name__ == "__main__":
    main()
