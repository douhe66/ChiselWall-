import json, sqlite3, sys, urllib.error, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
ACC = r"tests\fixtures\acceptance"
TOKEN = json.loads(open(ACC + r"\.run\config\config.json", encoding="utf-8").read())["api_token"]
def api(path):
    req = urllib.request.Request("http://127.0.0.1:8317" + path)
    req.add_header("X-Chisel-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
c = sqlite3.connect(ACC + r"\.run\chiselwall.db")
rows = c.execute("SELECT doc_id, status, rel_path FROM docs WHERE rel_path LIKE '%被排除-敏感工资表%'").fetchall()
c.close()
print("被排除文档 DB 记录:", rows)
if rows:
    doc_id = rows[0][0]
    code, body = api("/api/docs/" + str(doc_id))
    print("全文获取被排除文档: HTTP", code, "响应:", body[:150])
    code2, _ = api("/api/docs/" + str(doc_id) + "/open-location")
    print("打开位置被排除文档: HTTP", code2)
