"""修改重建 DB 级验证（reviewer）：排除 trigram 假阳性，直读 chunks。"""
import json, sqlite3, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ACC = Path(__file__).resolve().parent
DB = ACC / ".run" / "chiselwall.db"

def chunk_text_of(rel_frag):
    c = sqlite3.connect(str(DB))
    try:
        rows = c.execute("SELECT c.text FROM chunks c JOIN docs d ON d.doc_id=c.doc_id WHERE d.rel_path LIKE ?", ("%" + rel_frag + "%",)).fetchall()
        return "".join(r[0] for r in rows)
    finally:
        c.close()

exp_dir = ACC / "root" / "corpus" / "exp"
exp_dir.mkdir(parents=True, exist_ok=True)
f = exp_dir / "实验.md"
f.write_text("# 实验\nexp_dbword_alpha_777\n", encoding="utf-8")

def wait_text(rel, word, timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if word in chunk_text_of(rel):
            return True, time.time() - t0
        time.sleep(1)
    return False, time.time() - t0

ok, dt = wait_text("corpus/exp/实验.md", "exp_dbword_alpha_777")
print(f"新建入库: {ok} ({dt:.0f}s)")
f.write_text("# 实验\nexp_dbword_beta_888\n", encoding="utf-8")
ok, dt = wait_text("corpus/exp/实验.md", "exp_dbword_beta_888")
print(f"修改重建(新词入库): {ok} ({dt:.0f}s)")
txt = chunk_text_of("corpus/exp/实验.md")
print(f"旧词已清除: {'exp_dbword_alpha_777' not in txt}")
print(f"docs 记录:")
c = sqlite3.connect(str(DB))
for r in c.execute("SELECT rel_path, status, size, hash, chunk_count FROM docs WHERE rel_path LIKE '%corpus/exp%'"):
    print(" ", r)
c.close()
f.unlink()
time.sleep(10)
c = sqlite3.connect(str(DB))
rows = c.execute("SELECT COUNT(*) FROM docs WHERE rel_path LIKE '%corpus/exp%'").fetchone()
print("删除后 docs 记录数:", rows[0])
c.close()
