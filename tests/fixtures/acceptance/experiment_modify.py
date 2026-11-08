"""增量修改重现实验（reviewer）：定位 AC-05-02 失败根因。"""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8317"
ACC = Path(__file__).resolve().parent
TOKEN = json.loads((ACC / ".run" / "config" / "config.json").read_text(encoding="utf-8"))["api_token"]

def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("X-Chisel-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=30) as r:
        return json.loads(r.read().decode())

def rels(q):
    return [h["rel_path"] for h in api("/api/search?q=" + urllib.parse.quote(q) + "&topk=50")["hits"]]

exp_dir = ACC / "root" / "corpus" / "exp"
exp_dir.mkdir(parents=True, exist_ok=True)
f = exp_dir / "实验.md"

print("== 场景A：内容等长覆盖（size 不变） ==")
f.write_text("# 实验\nexp_word_alpha_0001\n", encoding="utf-8")
t0 = time.time()
while time.time() - t0 < 30 and not ("corpus/exp/实验.md" in rels("exp_word_alpha_0001")):
    time.sleep(1)
print(f"alpha 可搜: {'corpus/exp/实验.md' in rels('exp_word_alpha_0001')} ({time.time()-t0:.0f}s)")
st1 = f.stat()
f.write_text("# 实验\nexp_word_beta_000001\n", encoding="utf-8")
st2 = f.stat()
print(f"mtime 变化: {st1.st_mtime != st2.st_mtime} (前={st1.st_mtime!r} 后={st2.st_mtime!r}), size 变化: {st1.st_size != st2.st_size}")
t0 = time.time()
while time.time() - t0 < 45 and not ("corpus/exp/实验.md" in rels("exp_word_beta_000001")):
    time.sleep(1)
print(f"beta 可搜: {'corpus/exp/实验.md' in rels('exp_word_beta_000001')} ({time.time()-t0:.0f}s)")
print(f"alpha 残留: {'corpus/exp/实验.md' in rels('exp_word_alpha_0001')}")

print("== 场景B：内容不等长覆盖（size 变化） ==")
f.write_text("# 实验\nexp_word_gamma_12345678_更长\n", encoding="utf-8")
t0 = time.time()
while time.time() - t0 < 45 and not ("corpus/exp/实验.md" in rels("exp_word_gamma_12345678")):
    time.sleep(1)
print(f"gamma 可搜: {'corpus/exp/实验.md' in rels('exp_word_gamma_12345678')} ({time.time()-t0:.0f}s)")

print("== 场景C：删除后重建（先删再建） ==")
f.unlink()
t0 = time.time()
while time.time() - t0 < 30 and ("corpus/exp/实验.md" in rels("exp_word_gamma_12345678")):
    time.sleep(1)
print(f"gamma 删除后不可搜: {'corpus/exp/实验.md' not in rels('exp_word_gamma_12345678')} ({time.time()-t0:.0f}s)")
f.write_text("# 实验\nexp_word_delta_4321\n", encoding="utf-8")
t0 = time.time()
while time.time() - t0 < 45 and not ("corpus/exp/实验.md" in rels("exp_word_delta_4321")):
    time.sleep(1)
print(f"delta 重建可搜: {'corpus/exp/实验.md' in rels('exp_word_delta_4321')} ({time.time()-t0:.0f}s)")
# 清理
f.unlink()
