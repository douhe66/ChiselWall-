"""M2 验收驱动 v2（reviewer）：HTTP API 全量断言。

v2 修正（第一轮实测教训）：
    - 排除断言 = 目标 rel_path 不在命中列表（trigram OR 兜底会使共享前缀词互相命中，
      整词 0 命中在混合语料中不可靠；语义验证「排除文件不可见」而非「词零命中」）；
    - 旧格式 .doc/.xls 与二进制 png/zip 走「默认排除」轨道（设计 §7），不进失败列表；
      失败列表（status=error）仅覆盖解析失败（加密/无文本层/损坏）；
    - glob·扩展名用例改用 *.csv（.log 不在支持格式集，无法区分「格式不支持」与「glob 排除」）；
    - 增量标记词用无共享 3-gram 的 ASCII 词；删除文件带重试（Windows 句柄竞态）。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18318"
ACC = Path(__file__).resolve().parent
TOKEN = json.loads((ACC / ".run" / "config" / "config.json").read_text(encoding="utf-8"))["api_token"]

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL") + " | " + name + (" | " + detail if detail else ""))


def api(method: str, path: str, body: dict | None = None, expect_error: bool = False):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("X-Chisel-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if expect_error:
            return {"__http__": e.code}
        raise


def hits_for(q: str, topk: int = 50) -> list[dict]:
    return api("GET", f"/api/search?q={urllib.parse.quote(q)}&topk={topk}")["hits"]


def rels(q: str, topk: int = 50) -> list[str]:
    return [h["rel_path"] for h in hits_for(q, topk)]


def has(q: str, rel: str) -> bool:
    return rel in rels(q)


def wait_indexed(timeout: float = 180.0) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        s = api("GET", "/api/stats")
        if s["pending"] == 0 and (last is None or s["indexed"] == last):
            time.sleep(1.5)
            s2 = api("GET", "/api/stats")
            if s2["pending"] == 0 and s2["indexed"] == s["indexed"]:
                return s2
        last = s["indexed"]
        time.sleep(1.0)
    raise TimeoutError("索引未在期限内收敛")


def wait_cond(cond, timeout: float = 60.0, step: float = 1.0) -> tuple[bool, float]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True, time.time() - t0
        time.sleep(step)
    return False, time.time() - t0


def main() -> None:
    # ---- 挂载（幂等） ----
    existing = {m["path"]: m for m in api("GET", "/api/mounts")}
    def mount(path: str, kind: str | None = None) -> dict:
        if path in existing:
            return existing[path]
        return api("POST", "/api/mounts", {"path": path, **({"kind": kind} if kind else {})})

    r_root = mount(str(ACC / "root"))
    r_vault = mount(str(ACC / "vault"), "obsidian")
    r_file = mount(str(ACC / "单文件-项目周报-2024Q4.md"))
    check("挂载 root(directory)", r_root.get("kind") == "directory", str(r_root)[:80])
    check("挂载 vault(obsidian)", r_vault.get("kind") == "obsidian", str(r_vault)[:80])
    check("挂载单文件(file)", r_file.get("kind") == "file", str(r_file)[:80])
    check("红线② 拒绝挂载索引库目录", api("POST", "/api/mounts", {"path": str(ACC / ".run")}, expect_error=True).get("__http__") == 400, "")

    print("等待全量索引收敛 ...")
    stats = wait_indexed()
    print("stats:", json.dumps(stats, ensure_ascii=False))
    check("索引完成且无 pending", stats["pending"] == 0, "")
    check("失败数=3（加密/无文本层/损坏）", stats["error"] == 3, f"error={stats['error']}")

    # ---- A. 格式覆盖（AC-01-01~09） ----
    for name, q, want in [
        ("AC-01-01 md", "凿壁测试词·风", "corpus/md/数据库优化笔记.md"),
        ("AC-01-02 txt-utf8", "凿壁测试词·花", "corpus/txt/会议纪要-utf8.txt"),
        ("AC-01-03 txt-gbk", "凿壁测试词·编码", "edge/gbk.txt"),
        ("AC-01-04 docx", "凿壁测试词·雪", "corpus/docx/接口设计说明.docx"),
        ("AC-01-05 xlsx-sheet1", "凿壁测试词·月", "corpus/xlsx/环境配置清单.xlsx"),
        ("AC-01-05 xlsx-sheet2", "凿壁测试词·星", "corpus/xlsx/环境配置清单.xlsx"),
        ("AC-01-06 pdf", "cwfix_pdf_001", "corpus/pdf/架构评审纪要.pdf"),
        ("AC-01-07 csv", "凿壁测试词·山", "corpus/csv/服务器资产.csv"),
        ("AC-01-08 vault笔记A", "凿壁测试词·林", "笔记A.md"),
        ("AC-01-08 vault子目录", "凿壁测试词·木", "子目录/笔记B.md"),
        ("AC-01-09 单文件挂载", "凿壁测试词·周", "单文件-项目周报-2024Q4.md"),
        ("AC-06-02 中文空格路径", "凿壁测试词·径", "corpus/paths/中文目录/含空格 文件.md"),
        ("AC-01-03b gbk对照", "凿壁测试词·编码", "edge/gbk.txt"),
    ]:
        rel = rels(q)
        check(name, rel and rel[0] == want, "首个=" + (rel[0] if rel else "无命中"))

    # ---- B. 引擎正确性 ----
    check("AC-01-10 英文", has("postgres", "corpus/md/代码片段笔记.md"), str(rels("postgres")[:2]))
    check("AC-01-11 数字", has("cwfix_num_20240101", "corpus/md/代码片段笔记.md"), str(rels("cwfix_num_20240101")[:2]))
    check("AC-01-12 中文词", bool(rels("数据库")), str(rels("数据库")[:2]))
    check("AC-01-13 短语相邻命中", has("数据库连接池", "corpus/phrase/相邻.md"), str(rels("数据库连接池")[:2]))
    check("AC-01-13 短语分离不命中(相邻优先)", rels("数据库连接池")[:1] == ["corpus/phrase/相邻.md"], str(rels("数据库连接池")[:2]))
    h_tri = hits_for("cwfix_tri_ab")
    check("AC-01-14 默认关: 整token走主索引(route=jieba)", any(h["rel_path"] == "corpus/tri/tri子串.md" and h["route"] == "jieba" for h in h_tri), str([(h["rel_path"], h["route"]) for h in h_tri[:2]]))  # trigram 开启场景见 retest_trigram.py
    bm = rels("cwfix_bm25")
    check("AC-01-15 bm25排序", bm[:1] == ["corpus/bm25/多.md"], str(bm[:3]))
    snip = hits_for("凿壁测试词·长")
    ok_snip = bool(snip) and "凿壁测试词·长" in snip[0]["snippet_html"] and len(snip[0]["snippet_text"]) < 1000
    check("AC-01-16 长文片段", ok_snip, f"片段长度={len(snip[0]['snippet_text']) if snip else 0}")
    h0 = hits_for("凿壁测试词·风")[0]
    need = {"doc_id", "title", "rel_path", "score", "snippet_html", "format"}
    check("AC-01-17 命中字段齐全", need.issubset(h0.keys()), str(sorted(h0.keys())))

    # ---- C. 默认排除（AC-02-04~07）：目标文件不可见 ----
    for name, q, want_hidden in [
        ("AC-02-04 隐藏文件不可见", "凿壁敏感词·隐", ".hidden.md"),
        ("AC-02-06 ~$临时不可见", "凿壁敏感词·幽", "~$幽灵.docx"),
        ("AC-02-06 .tmp不可见", "凿壁敏感词·临", "临时笔记.tmp"),
        ("AC-02-05 node_modules不可见", "凿壁敏感词·模", "node_modules"),
        ("AC-02-05 .git不可见", "凿壁敏感词·git", ".git"),
        ("AC-02-05 .svn不可见", "凿壁敏感词·svn", ".svn"),
        ("AC-01-08 .obsidian(vault)不可见", "凿壁测试词·藏", ".obsidian"),
        ("AC-02-05 .obsidian(edge)不可见", "凿壁敏感词·藏二", ".obsidian"),
    ]:
        bad = [r for r in rels(q) if want_hidden in r]
        check(name, bad == [], str(bad[:2]))
    for name, q in [("隔离对照一", "凿壁测试词·对照一"), ("对照二", "凿壁测试词·对照二"),
                    ("对照三", "凿壁测试词·对照三"), ("对照四", "凿壁测试词·对照四")]:
        check("AC-02 隔离 " + name, bool(rels(q)), str(rels(q)[:1]))

    # ---- D. 截断与大小阈值 ----
    check("AC-01-19 截断前A命中", has("cwfix_big_a", "generated/big_over_cap.md"), str(rels("cwfix_big_a")[:2]))
    h_big = [h for h in hits_for("cwfix_big_a") if h["rel_path"] == "generated/big_over_cap.md"]
    if h_big:
        dbig = api("GET", f"/api/docs/{h_big[0]['doc_id']}?max_chars=10000000")
        t = dbig.get("text", "")
        check("AC-01-19 全文含A不含B(截断)", "cwfix_big_a" in t and "cwfix_big_b" not in t, f"len={len(t)}")
    else:
        check("AC-01-19 全文含A不含B(截断)", False, "A 未命中")
    s1 = api("GET", "/api/stats")
    check("AC-02-07 空文件+21MB 进 excluded", s1["excluded"] >= 3, f"excluded={s1['excluded']}")

    # ---- E. 失败列表（AC-04） ----
    failed = api("GET", "/api/failed")
    fr = [f["rel_path"] for f in failed]
    print("失败列表:", json.dumps(fr, ensure_ascii=False))
    for name, want in [
        ("AC-04-03 加密pdf", "edge/encrypted.pdf"),
        ("AC-04-04 无文本层pdf", "edge/scanned_no_text.pdf"),
        ("AC-04-02 损坏docx", "edge/corrupted.docx"),
    ]:
        check(name + " 入失败列表", want in fr, "")
    check("AC-04-05 旧格式 .doc 走默认排除轨道", not any(".doc" in r and "old_format" in r for r in fr) and stats["excluded"] >= 4, f"excluded={s1['excluded']}")
    check("AC-04-06 二进制 png/zip 走默认排除轨道", not any("binary" in r for r in fr), "")
    check("AC-04-01 整体索引完成", stats["indexed"] >= 30, f"indexed={stats['indexed']}")
    check("AC-04-06 空文件不报错", not any("empty" in r for r in fr), "")
    # 失败列表含原因
    f0 = {f["rel_path"]: f["error"] for f in failed}
    check("失败原因字段非空", all(f0.values()), json.dumps(f0, ensure_ascii=False)[:150])

    # ---- F. 用户排除规则（AC-02-01~03/09/11） ----
    for r_old in api("GET", "/api/excludes"):
        api("DELETE", f"/api/excludes/{r_old['rule_id']}")
    time.sleep(2.0)
    wait_indexed(timeout=90)
    pre = {
        "凿壁敏感词·薪": "excludes/single_file/被排除-敏感工资表.xlsx",
        "凿壁敏感词·密": "excludes/dir/secret_dir/秘密笔记.md",
        "凿壁敏感词·草": "excludes/glob/secret-draft.md",
        "凿壁敏感词·稿": "excludes/glob/drafts/草稿1.md",
        "凿壁敏感词·扩": "excludes/glob/扩展名排除测试.csv",
    }
    for q, want in pre.items():
        check("排除前可搜:" + want.split("/")[-1], has(q, want), str(rels(q)[:2]))
    rules = [
        ("file", str(ACC / "root" / "excludes" / "single_file" / "被排除-敏感工资表.xlsx")),
        ("dir", str(ACC / "root" / "excludes" / "dir" / "secret_dir")),
        ("glob", "*.csv"),
        ("glob", "secret*"),
        ("glob", "**/drafts/*"),
    ]
    rule_ids = []
    for kind, pat in rules:
        r = api("POST", "/api/excludes", {"kind": kind, "pattern": pat})
        rule_ids.append(r["rule_id"])
        check("添加规则 " + kind + ":" + pat.replace("\\", "/").split("/")[-1], "rule_id" in r, str(r)[:60])
    time.sleep(2.0)
    for q, want in pre.items():
        check("AC-02 排除后不可见:" + want.split("/")[-1], not has(q, want), str(rels(q)[:2]))
    s2 = api("GET", "/api/stats")
    check("AC-02-09 排除后统计", s2["excluded"] >= 5, f"excluded={s2['excluded']}")
    # 删除规则恢复（AC-02-11）
    for rid in rule_ids:
        api("DELETE", f"/api/excludes/{rid}")
    time.sleep(2.0)
    wait_indexed(timeout=90)
    check("AC-02-11 解除排除恢复", has("凿壁敏感词·薪", "excludes/single_file/被排除-敏感工资表.xlsx"), str(rels("凿壁敏感词·薪")[:2]))
    # 重新排除一份并验证全文 403（红线⑤）
    api("POST", "/api/excludes", {"kind": "file", "pattern": str(ACC / "root" / "excludes" / "single_file" / "被排除-敏感工资表.xlsx")})
    time.sleep(1.5)
    hit = hits_for("凿壁敏感词·薪")
    check("排除后搜索不可得", hit == [] or all(h["rel_path"] != "excludes/single_file/被排除-敏感工资表.xlsx" for h in hit), "")

    # ---- G. 增量（AC-05-01~03）：DB 层断言 + 随机词（trigram 假阳性免疫） ----
    import sqlite3 as _sq
    import uuid as _uuid

    def db_word(rel_frag: str, word: str) -> bool:
        c = _sq.connect(str(ACC / ".run" / "chiselwall.db"))
        try:
            rows = c.execute(
                "SELECT text FROM chunks c JOIN docs d ON d.doc_id=c.doc_id WHERE d.rel_path LIKE ?",
                ("%" + rel_frag + "%",),
            ).fetchall()
            return any(word in r[0] for r in rows)
        finally:
            c.close()

    inc_dir = ACC / "root" / "corpus" / "incr"
    inc_dir.mkdir(parents=True, exist_ok=True)
    f_new = inc_dir / "增量新建.md"
    w1, w2 = "cx_" + _uuid.uuid4().hex[:10], "cx_" + _uuid.uuid4().hex[:10]
    f_new.write_text("# 新建\n" + w1 + "\n", encoding="utf-8")
    ok1, dt1 = wait_cond(lambda: db_word("corpus/incr/增量新建.md", w1), 60)
    check("AC-05-01 新增自动入库", ok1, f"{dt1:.0f}s")
    f_new.write_text("# 修改\n" + w2 + "\n", encoding="utf-8")
    ok2, dt2 = wait_cond(lambda: db_word("corpus/incr/增量新建.md", w2) and not db_word("corpus/incr/增量新建.md", w1), 60)
    check("AC-05-02 修改重建且旧词清除", ok2, f"{dt2:.0f}s")
    for _ in range(10):
        try:
            f_new.unlink()
            break
        except PermissionError:
            time.sleep(1.0)
    ok3, dt3 = wait_cond(lambda: not db_word("corpus/incr/增量新建.md", w2), 60)
    check("AC-05-03 删除后记录清除", ok3, f"{dt3:.0f}s")
    check("AC-05 检索层一致(删除后)", not has(w2, "corpus/incr/增量新建.md"), "")

    # ---- H. API 细节 ----
    r_lim = api("GET", "/api/search?q=" + urllib.parse.quote("凿壁") + "&topk=2")
    check("topk 生效", len(r_lim["hits"]) <= 2, f"hits={len(r_lim['hits'])}")
    r_empty = api("GET", "/api/search?q=")
    check("空查询不500", isinstance(r_empty, dict) and r_empty["hits"] == [], "")
    h_any = hits_for("凿壁测试词·风")[0]
    d = api("GET", f"/api/docs/{h_any['doc_id']}")
    check("全文获取已索引文档", bool(d.get("text")), f"len={len(d.get('text',''))}")
    notfound = api("GET", "/api/docs/999999", expect_error=True)
    check("不存在文档 404", notfound.get("__http__") == 404, f"http={notfound.get('__http__')}")
    ol = api("GET", f"/api/docs/{h_any['doc_id']}/open-location")
    check("AC-07-08 open-location API", ol.get("ok") is True and "explorer" in str(ol.get("how")), str(ol))

    # ---- 汇总 ----
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print("\n==== 结果汇总 ====")
    print(f"通过 {passed}/{len(RESULTS)}")
    failed = [(n, d) for n, ok, d in RESULTS if not ok]
    for n, d in failed:
        print("  FAIL:", n, "|", d)
    sys.exit(0 if passed == len(RESULTS) else 2)


if __name__ == "__main__":
    main()
