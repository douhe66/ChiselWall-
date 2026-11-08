import sqlite3, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
c = sqlite3.connect(r"tests\fixtures\acceptance\.run\chiselwall.db")
print("--- incr docs ---")
for r in c.execute("SELECT doc_id, rel_path, status, mtime, size, chunk_count, truncated, error FROM docs WHERE rel_path LIKE '%incr%'"):
    print(r)
print("--- incr chunks ---")
for r in c.execute("SELECT chunk_id, seq, substr(text,1,80) FROM chunks WHERE doc_id IN (SELECT doc_id FROM docs WHERE rel_path LIKE '%incr%')"):
    print(r)
print("--- big_over_cap doc ---")
for r in c.execute("SELECT doc_id, status, chunk_count, truncated, size FROM docs WHERE rel_path LIKE '%big_over_cap%'"):
    print(r)
print("--- 磁盘 incr 目录 ---")
p = pathlib.Path(r"tests\fixtures\acceptance\root\corpus\incr")
print("存在:", p.exists())
if p.exists():
    for f in p.iterdir():
        print(" ", f.name, f.stat().st_size)
