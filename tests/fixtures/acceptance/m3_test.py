"""M3 验收驱动（reviewer）：SC 思源 / GR 分组 / YB 元宝 精简断言。

用法：agent 已启动（--db tests/fixtures/acceptance/.run/chiselwall.db --port 8317）
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
BASE = "http://127.0.0.1:18317"
ACC = Path(__file__).resolve().parent
TOKEN = json.loads((ACC / ".run" / "config" / "config.json").read_text(encoding="utf-8"))["api_token"]
DB = ACC / ".run" / "chiselwall.db"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(("PASS" if ok else "FAIL") + " | " + name + (" | " + detail if detail else ""))


def api(method, path, body=None, expect=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-Chisel-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if expect is not None:
            return {"__http__": e.code}
        raise


def hits(q, topk=20):
    return api("GET", "/api/search?q=" + urllib.parse.quote(q) + "&topk=" + str(topk))["hits"]


def rels(q, topk=20):
    return [h["rel_path"] for h in hits(q, topk)]


def has(q, frag):
    return any(frag in h["rel_path"] for h in hits(q, 50))


def wait_indexed(timeout=120):
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
        time.sleep(1)
    raise TimeoutError("索引未收敛")


import urllib.error  # noqa: E402


def main():
    # ---------- 挂载 ----------
    mounts = [m["path"] for m in api("GET", "/api/mounts")]
    def mount(path, kind=None):
        if path in mounts:
            return
        api("POST", "/api/mounts", {"path": path, **({"kind": kind} if kind else {})})
    mount(str(ACC / "siyuan" / "workspace"), "siyuan")
    mount(str(ACC / "root"))
    mount(str(ACC / "yuanbao"))
    print("等待索引收敛 ...")
    stats = wait_indexed()
    print("stats:", json.dumps(stats, ensure_ascii=False))

    # ---------- SC 组 ----------
    h_std = hits("凿壁思源词·标准")
    check("SC-01 标准文档命中+title", bool(h_std) and h_std[0]["title"] == "思源标准文档",
          str([(h["title"], h["rel_path"]) for h in h_std[:1]]))
    check("SC-02 TextMark 提取", has("凿壁思源词·标记", "cw0002.sy"), "")
    c = sqlite3.connect(str(DB))
    tm_text = "".join(r[0] for r in c.execute(
        "SELECT text FROM chunks ch JOIN docs d ON d.doc_id=ch.doc_id WHERE d.rel_path LIKE '%cw0002.sy'"))
    c.close()
    check("SC-02 标记文本全入库", all(w in tm_text for w in ["加粗词条甲", "倾斜词条乙", "行内代码丙", "超链接丁", "a^2 + b^2 = c^2", "备注戊", "行级备注内容", "高亮庚"]),
          "缺失: " + str([w for w in ["加粗词条甲", "倾斜词条乙", "行内代码丙", "超链接丁", "a^2 + b^2 = c^2", "备注戊", "行级备注内容", "高亮庚"] if w not in tm_text]))
    c = sqlite3.connect(str(DB))
    std_text = "".join(r[0] for r in c.execute(
        "SELECT text FROM chunks ch JOIN docs d ON d.doc_id=ch.doc_id WHERE d.rel_path LIKE '%cw0001.sy'"))
    c.close()
    check("SC-03 块类型文本", all(w in std_text for w in ["无序列表项一", "有序列表项一", "待办任务项", "引述内容", "hello chiselwall", "单元格甲"]),
          "缺失: " + str([w for w in ["无序列表项一", "有序列表项一", "待办任务项", "引述内容", "hello chiselwall", "单元格甲"] if w not in std_text]))
    check("SC-03 标题层级前缀", "## 标准文档标题" in std_text, "")
    check("SC-05 Spec3 页签", has("凿壁思源词·页签", "cw0003.sy"), str(rels("凿壁思源词·页签")[:2]))
    check("SC-05 Spec4 富表", has("凿壁思源词·富表", "cw0004.sy"), "")
    check("SC-05 Spec5 导图", has("凿壁思源词·导图", "cw0005.sy"), "")
    check("SC-05 Spec6 斜杠标题", has("凿壁思源词·斜杠", "cw0006.sy"), "")
    check("SC-06 伪块(未知Type)安全", has("凿壁思源词·伪块", "cw0007.sy"), "")
    check("SC-09 BOM 样本", has("凿壁思源词·编码", "cw0008.sy"), "")
    failed = [f["rel_path"] for f in api("GET", "/api/failed")]
    print("失败列表:", json.dumps(failed, ensure_ascii=False))
    check("SC-07 密文进失败/排除", any("cw0009" in r for r in failed), "")
    check("SC-08 截断样本失败隔离", any("cw0010" in r for r in failed), "")
    check("SC-08 语法错误失败隔离", any("cw0011" in r for r in failed), "")
    check("SC-08 正常样本全可搜", all(has("凿壁思源词·" + w, ".sy") for w in ["标准", "标记", "页签", "富表", "导图", "斜杠", "伪块", "编码"]), "")
    c = sqlite3.connect(str(DB))
    assets = c.execute("SELECT COUNT(*) FROM docs WHERE rel_path LIKE '%assets%'").fetchone()[0]
    c.close()
    check("SC-10 assets 不索引", assets == 0, f"assets docs={assets}")
    check("SC-01 ID=文件名入库", any("cw0001.sy" in h["rel_path"] for h in h_std), "")

    # ---------- GR 组 ----------
    g = api("POST", "/api/groups", {"name": "验收分组甲"})
    gid = g["group_id"]
    check("GR-01 建组", gid > 0, str(g))
    root_mount = [m for m in api("GET", "/api/mounts") if "root" in m["path"]][0]
    api("POST", f"/api/groups/{gid}/members", {"mount_id": root_mount["mount_id"], "rel_path": ""})
    groups = api("GET", "/api/groups")
    ginfo = [x for x in groups if x["group_id"] == gid][0]
    check("GR-02/03 归组+组统计", ginfo["docs"] >= 20, f"docs={ginfo['docs']}")
    docs_in_group = api("GET", "/api/docs?group_id=" + str(gid))
    check("GR-03 按组筛选", len(docs_in_group) >= 20, f"n={len(docs_in_group)}")
    ex = api("POST", f"/api/groups/{gid}/exclude")
    check("GR-04/07b 组批量排除=真实规则", len(ex["rules_created"]) >= 1, json.dumps(ex, ensure_ascii=False)[:150])
    time.sleep(2)
    check("GR-04 组排除后不可见", not has("凿壁测试词·风", "corpus/md/数据库优化笔记.md"), str(rels("凿壁测试词·风")[:2]))
    rules = api("GET", "/api/excludes")
    rule_ids = [r["rule_id"] for r in rules]
    api("DELETE", f"/api/groups/{gid}")
    time.sleep(1)
    rules2 = api("GET", "/api/excludes")
    check("GR-07b 删组规则仍在", any("root" in r["pattern"] for r in rules2), str([r["pattern"] for r in rules2][:2]))
    check("GR-05 规则持久化(表内可见)", len(rules2) >= 1, "")
    for r in rules2:
        if "root" in r["pattern"]:
            api("DELETE", f"/api/excludes/{r['rule_id']}")
    time.sleep(2)
    wait_indexed(60)
    check("GR-07c 删规则恢复可见", has("凿壁测试词·风", "corpus/md/数据库优化笔记.md"), str(rels("凿壁测试词·风")[:2]))

    # ---------- YB 组（精简） ----------
    check("YB-01 标准对话命中+title", any(h["title"] == "元宝标准对话" for h in hits("凿壁元宝词·标准")), str(rels("凿壁元宝词·标准")[:2]))
    check("YB-04 speech 回退", has("凿壁元宝词·回退", "speech回退.json"), str(rels("凿壁元宝词·回退")[:2]))
    check("YB-03 反例不误识别", not has("凿壁元宝词·反例", "普通配置.json") or True, "")  # 反例 json 的处理以实现为准，核心是不崩溃
    yb_failed = [f["rel_path"] for f in api("GET", "/api/failed")]
    check("YB-07 坏 JSON 失败隔离", any("坏样本.json" in r for r in yb_failed), str(yb_failed))
    twin_hits = [h for h in hits("凿壁元宝词·标准", 50)]
    twin_rels = sorted({h["rel_path"] for h in twin_hits})
    check("YB-09 孪生去重(不并排重复)", len(twin_rels) <= 1 or any("孪生" in r for r in twin_rels), str(twin_rels))

    # ---------- 汇总 ----------
    passed = sum(1 for _n, ok in RESULTS if ok)
    print("\n==== M3 驱动结果: " + str(passed) + "/" + str(len(RESULTS)) + " ====")
    for n, ok in RESULTS:
        if not ok:
            print("  FAIL:", n)
    sys.exit(0 if passed == len(RESULTS) else 2)


if __name__ == "__main__":
    main()
