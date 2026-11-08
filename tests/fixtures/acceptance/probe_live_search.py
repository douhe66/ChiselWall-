import json, sys, urllib.parse, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
TOKEN = json.loads(open(r"C:\Users\Administrator\.chiselwall\config.json", encoding="utf-8").read())["api_token"]
def api(path):
    req = urllib.request.Request("http://127.0.0.1:8371" + path)
    req.add_header("X-Chisel-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
for q in ["数据库", "会议", "笔记", "方案", "todo", "代码"]:
    r = api("/api/search?q=" + urllib.parse.quote(q) + "&topk=5")
    hits = r["hits"]
    print(f"--- {q}: {len(hits)} 命中 ---")
    for h in hits[:3]:
        print("   ", h["route"], "|", h["format"], "|", h["title"][:24], "|", h["rel_path"][:60])
        print("      片段:", h["snippet_text"][:70].replace("\n", " "))
# 思源格式命中统计
r2 = api("/api/search?q=" + urllib.parse.quote("数据库") + "&topk=20")
sy_hits = [h for h in r2["hits"] if h["format"] == "sy"]
print(f"\n数据库查询中 format=sy 命中数: {len(sy_hits)}/20")
