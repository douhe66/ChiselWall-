"""task-11 复核：F1（索引期检索 max≤500ms）+ F2（默认配置 db/文本 ≤500%）。

用法：agent 已启动（默认配置，trigram 关）。脚本复制 load2 挂载制造真实索引负载。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8317"
ACC = Path(__file__).resolve().parent
TOKEN = json.loads((ACC / ".run" / "config" / "config.json").read_text(encoding="utf-8"))["api_token"]
DB = ACC / ".run" / "chiselwall.db"
QUERIES = ["凿壁", "数据库", "postgres", "cwfix_num_20240101", "数据库连接池", "ab", "凿壁测试词·风", "敏感词", "unknown_xyz_999", "chiselwall"]


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-Chisel-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def stats():
    return api("/api/stats")


def main():
    # 清理既有 load2
    for m in api("/api/mounts"):
        if "load2" in m["path"]:
            api("/api/mounts/" + str(m["mount_id"]), "DELETE")
    load2 = ACC / "load2"
    if load2.exists():
        shutil.rmtree(load2)
    shutil.copytree(ACC / "root", load2)
    print("load2 复制完成，挂载:", api("/api/mounts", "POST", {"path": str(load2)}))

    # F1：索引期并发检索
    lat: list[float] = []
    stop = threading.Event()

    def bg():
        i = 0
        while not stop.is_set() and i < 100:
            q = QUERIES[i % len(QUERIES)]
            t0 = time.perf_counter()
            try:
                api("/api/search?q=" + urllib.parse.quote(q) + "&topk=8")
            except Exception:
                lat.append(99999.0)
                break
            lat.append((time.perf_counter() - t0) * 1000)
            i += 1

    th = threading.Thread(target=bg)
    th.start()
    # 等索引收敛（索引与检索并发进行）
    t0 = time.time()
    while time.time() - t0 < 240:
        s = stats()
        if s["pending"] == 0 and s["indexed"] >= 60:
            break
        time.sleep(1)
    stop.set()
    th.join(timeout=30)
    print("收敛 stats:", json.dumps(stats(), ensure_ascii=False))
    lat.sort()
    p50 = round(lat[len(lat)//2], 2) if lat else -1
    p95 = round(lat[int(len(lat)*0.95)], 2) if lat else -1
    mx = round(max(lat), 2) if lat else -1
    print(f"F1 索引期并发检索: n={len(lat)} p50={p50}ms p95={p95}ms max={mx}ms")
    f1_ok = len(lat) >= 50 and mx <= 500
    print("F1 判定:", "PASS" if f1_ok else "FAIL", f"(复测点 max≤500ms)")

    # F2：默认配置 db/文本比
    time.sleep(2)
    db_size = DB.stat().st_size
    c = sqlite3.connect(str(DB))
    text_chars = c.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM chunks").fetchone()[0]
    trigram_rows = c.execute("SELECT COUNT(*) FROM chunks_trigram").fetchone()[0]
    c.close()
    text_bytes_est = text_chars * 3  # 中文 UTF-8 按 3 字节估算
    ratio = db_size / text_bytes_est
    print(f"F2 磁盘: db={db_size/1048576:.1f}MB 文本≈{text_bytes_est/1048576:.1f}MB(估算) 比值={ratio:.1%} trigram行数={trigram_rows}")
    f2_ok = ratio <= 5.0 and trigram_rows == 0
    print("F2 判定:", "PASS" if f2_ok else "FAIL", "(复测点 ≤500% 且 trigram 默认关表空)")

    # 清理 load2
    for m in api("/api/mounts"):
        if "load2" in m["path"]:
            api("/api/mounts/" + str(m["mount_id"]), "DELETE")
    if load2.exists():
        shutil.rmtree(load2)
    print("load2 已清理")
    sys.exit(0 if (f1_ok and f2_ok) else 2)


if __name__ == "__main__":
    main()
