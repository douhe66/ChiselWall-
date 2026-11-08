"""M2 资源测量（reviewer）：psutil 三段采样 + 检索延迟 + 优先级 + 磁盘。

场景：基线60s → 挂载 load2（含 15MB 大文本，制造真实索引负载）→ 索引期采样至收敛
      → 索引后空闲 30s；索引期与空闲期各测 100 次 HTTP 检索延迟。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psutil

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8317"
ACC = Path(__file__).resolve().parent
PID = int(sys.argv[1])
TOKEN = json.loads((ACC / ".run" / "config" / "config.json").read_text(encoding="utf-8"))["api_token"]
DB = ACC / ".run" / "chiselwall.db"

QUERIES = ["凿壁", "数据库", "postgres", "cwfix_num_20240101", "数据库连接池", "cwfix_tri_ab", "ab", "凿壁测试词·风", "unknown_xyz_999", "敏感词"]


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-Chisel-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def stats():
    return api("/api/stats")


def search_latency(n=100, verbose=False):
    lat = []
    for i in range(n):
        q = QUERIES[i % len(QUERIES)]
        t0 = time.perf_counter()
        api("/api/search?q=" + urllib.parse.quote(q) + "&topk=8")
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    return {"n": len(lat), "p50": round(lat[len(lat)//2], 2), "p95": round(lat[int(len(lat)*0.95)], 2), "max": round(lat[-1], 2)}


def sample(duration: float, label: str) -> dict:
    proc = psutil.Process(PID)
    proc.cpu_percent(interval=None)  # 预热
    t0 = time.time()
    cpus, rsss, thrs = [], [], []
    while time.time() - t0 < duration:
        me = proc.cpu_percent(interval=None)
        kids = sum((p.cpu_percent(interval=None) or 0.0) for p in proc.children(recursive=True))
        cpus.append(me + kids)
        rsss.append(proc.memory_info().rss / 1048576)
        thrs.append(proc.num_threads())
        time.sleep(1)
    cpus.sort()
    rsss.sort()
    out = {
        "label": label,
        "dur": round(time.time() - t0, 1),
        "cpu_p50": round(cpus[len(cpus)//2], 1),
        "cpu_p95": round(cpus[int(len(cpus)*0.95)], 1),
        "cpu_max": round(cpus[-1], 1),
        "rss_p50": round(rsss[len(rsss)//2], 1),
        "rss_peak": round(rsss[-1], 1),
        "threads_max": max(thrs),
        "samples": len(cpus),
    }
    print(label, "->", json.dumps(out, ensure_ascii=False))
    return out


def main() -> None:
    proc = psutil.Process(PID)
    print("进程:", proc.name(), "nice(priority class):", proc.nice())
    print("线程数:", proc.num_threads())

    # ① 基线（空闲）
    base = sample(60, "①基线空闲60s")

    # ② 制造索引负载：复制语料到 load2 并挂载
    load2 = ACC / "load2"
    if load2.exists():
        shutil.rmtree(load2)
    shutil.copytree(ACC / "root", load2)
    print("load2 复制完成, 挂载:", api("/api/mounts", "POST", {"path": str(load2)}))
    # 索引期采样（同时并发检索测「检索优先」）
    import threading
    idx_lat_holder = {}
    def bg_search():
        idx_lat_holder["r"] = search_latency(50)
    th = threading.Thread(target=bg_search)
    th.start()
    idx = sample(60, "②索引期60s")
    # 等收敛（最长再 180s），期间继续采样
    t0 = time.time()
    while time.time() - t0 < 180:
        s = stats()
        if s["pending"] == 0:
            break
        time.sleep(2)
    th.join(timeout=10)
    print("索引收敛 stats:", json.dumps(stats(), ensure_ascii=False))
    print("索引期并发检索延迟:", json.dumps(idx_lat_holder.get("r", {}), ensure_ascii=False))

    # ③ 索引后空闲
    idle = sample(30, "③索引后空闲30s")

    # ④ 空闲检索延迟
    lat_idle = search_latency(100)
    print("空闲检索延迟(HTTP):", json.dumps(lat_idle, ensure_ascii=False))

    # ⑤ 磁盘
    db_size = DB.stat().st_size
    c = sqlite3.connect(str(DB))
    text_bytes = c.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM chunks").fetchone()[0]
    c.close()
    print(f"磁盘: db={db_size/1048576:.1f}MB, chunks原文字节={text_bytes/1048576:.1f}MB, 比值={db_size/text_bytes:.2%}")

    # ⑥ 进程优先级核对（Windows BELOW_NORMAL = 16384）
    print("优先级类:", proc.nice(), "（期望 16384 = BELOW_NORMAL_PRIORITY_CLASS）")


if __name__ == "__main__":
    main()
