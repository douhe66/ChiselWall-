import json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
TOKEN = json.loads(open(r"C:\Users\Administrator\.chiselwall\config.json", encoding="utf-8").read())["api_token"]
def api(path):
    req = urllib.request.Request("http://127.0.0.1:8371" + path)
    req.add_header("X-Chisel-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
print("health:", api("/api/health"))
print("stats:", json.dumps(api("/api/stats"), ensure_ascii=False))
print("mounts:", json.dumps(api("/api/mounts"), ensure_ascii=False)[:600])
print("failed:", json.dumps(api("/api/failed"), ensure_ascii=False)[:400])
