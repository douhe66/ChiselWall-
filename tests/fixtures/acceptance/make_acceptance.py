"""M2 验收语料生成（reviewer，幂等）。配套 docs/review/m2-testdata-plan.md。

生成 tests/fixtures/acceptance/：
    root/    主挂载（directory）：corpus 各格式 + excludes 用户规则样本 + edge 边界样本
    vault/   Obsidian 挂载（.obsidian 默认排除 + 笔记）
    单文件-项目周报-2024Q4.md   单文件挂载（AC-01-09）
    generated/  大文件（6MB 截断 / 21MB 默认排除）

中文 PDF 限制：环境无 reportlab/LibreOffice，文本层 PDF 用 ASCII 手工构造
（builder make_fixtures 同手法）；PDF 解析链路验证不受影响，pypdf 中文抽取为上游库职责。
"""
from __future__ import annotations

import io
import random
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # acceptance/ -> fixtures/ -> tests/ -> chiselwall/
ACC = ROOT / "tests" / "fixtures" / "acceptance"


def w(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def wb(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def simple_pdf(path: Path, lines: list[str]) -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    y = 760
    ops = []
    for line in lines:
        ops.append(f"BT /F1 12 Tf 50 {y} Td ({esc(line)}) Tj ET")
        y -= 24
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n").encode()
    path.write_bytes(bytes(out))


def make_encrypted_pdf() -> None:
    src = ACC / "tmp_plain.pdf"
    simple_pdf(src, ["ChiselWall encrypted pdf test", "cwfix_enc_001 inside."])
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    try:
        writer.encrypt("cwtest123", "cwtest123", algorithm="RC4-40")
    except TypeError:
        writer.encrypt("cwtest123", "cwtest123")
    with open(ACC / "root" / "edge" / "encrypted.pdf", "wb") as f:
        writer.write(f)
    src.unlink()


def make_blank_pdf() -> None:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(ACC / "root" / "edge" / "scanned_no_text.pdf", "wb") as f:
        writer.write(f)


def corpus() -> None:
    c = ACC / "root" / "corpus"
    for sub in ("md", "txt", "docx", "xlsx", "pdf", "csv", "phrase", "bm25", "tri", "paths"):
        (c / sub).mkdir(parents=True, exist_ok=True)
    (c / "paths" / "中文目录").mkdir(parents=True, exist_ok=True)
    w(c / "md" / "数据库优化笔记.md",
      """# 数据库优化笔记

本周整理了部门数据库的慢查询清单，重点是索引失效的几个场景。

凿壁测试词·风：查询语句需要先看执行计划再决定是否加索引。

## 增量索引

增量索引依赖文件签名校验，mtime 和 size 变化时才重新计算哈希。
""")
    w(c / "md" / "markdown长文.md", ("这是一份用于片段窗口验证的长文档。\n\n## 第一章 背景\n\n" + "部门的文档散落在各个同事的电脑里，相互不知道对方有什么。\n\n" * 12 + "## 第二章 核心\n\n" + "中间段落：" + "凿壁测试词·长" + " 出现在文档中部位置，用于验证上下文片段窗口截取。\n\n" + "## 第三章 结尾\n\n" + "后续内容用于验证窗口不会从头开始截取。\n\n" * 15))
    w(c / "md" / "代码片段笔记.md",
      """# 代码片段

数据库连接示例：

    psql -h postgres -U admin

部署流水线编号 cwfix_num_20240101 已归档。

凿壁测试词·码：SQLite FTS5 与 jieba 影子列配合做中文检索。
""")
    w(c / "md" / "frontmatter笔记.md",
      """---
tags: [chiselwall]
created: 2024-01-01
---
# 正文

frontmatter 里的 cwfix_yaml_tag 不应干扰正文检索。

凿壁测试词·幕：本笔记带 YAML 头部，验证 frontmatter 剥离后正文正常入库。
""")
    w(c / "txt" / "会议纪要-utf8.txt", "会议纪要：讨论了凿壁测试词·花 的索引方案。\n决定：先做 FTS5 关键词，向量检索后续可选。\n")
    w(c / "txt" / "部署手册.txt", "部署手册：本机安装后挂载目录即可搜索。\n标记：凿壁测试词·部署。\n")
    w(c / "csv" / "服务器资产.csv", "名称,IP,备注\n数据库主库,10.0.0.1,凿壁测试词·山\n")
    w(c / "phrase" / "相邻.md", "# 相邻\n数据库连接池的配置要点：最大连接数、超时时间。凿壁测试词·短语甲。\n")
    w(c / "phrase" / "分离.md", "# 分离\n部门使用统一的数据库，另外平台组维护自己的连接池。凿壁测试词·短语乙。\n")
    w(c / "bm25" / "少.md", "# 少\ncwfix_bm25 只出现一次。\n")
    w(c / "bm25" / "中.md", "# 中\ncwfix_bm25 出现三次：cwfix_bm25 一、cwfix_bm25 二、cwfix_bm25 三。\n")
    w(c / "bm25" / "多.md", "# 多\n" + "cwfix_bm25 " * 10 + "\n")
    w(c / "tri" / "tri子串.md", "# tri\n包含子串标记 cwfix_tri_ab 用于 trigram 兜底验证。\n")
    w(c / "paths" / "中文目录" / "含空格 文件.md", "# 路径\n凿壁测试词·径：中文目录与空格文件名应可索引可命中。\n")
    # docx
    import docx as _docx
    d = _docx.Document()
    d.add_heading("接口设计说明", level=1)
    d.add_paragraph("本接口用于本地知识检索，参数 q 为查询词。")
    d.add_paragraph("凿壁测试词·雪 出现在正文段落中。")
    d.save(str(c / "docx" / "接口设计说明.docx"))
    d2 = _docx.Document()
    d2.add_paragraph("故障复盘：索引任务在某台机器上卡顿。")
    d2.add_paragraph("凿壁测试词·雷 用于验证 docx 第二份样本。")
    d2.save(str(c / "docx" / "故障复盘.docx"))
    # xlsx 多 sheet
    import openpyxl as _xl
    wb1 = _xl.Workbook()
    ws = wb1.active
    ws.title = "sheet1"
    ws.append(["条目", "值"])
    ws.append(["标记", "凿壁测试词·月"])
    ws2 = wb1.create_sheet("sheet2")
    ws2.append(["条目", "值"])
    ws2.append(["标记", "凿壁测试词·星"])
    wb1.save(str(c / "xlsx" / "环境配置清单.xlsx"))
    wb2 = _xl.Workbook()
    wb2.active.append(["值班", "人员"])
    wb2.active.append(["周一", "凿壁测试词·值"])
    wb2.save(str(c / "xlsx" / "值班表.xlsx"))
    # pdf（ASCII 文本层）
    simple_pdf(c / "pdf" / "架构评审纪要.pdf", ["ChiselWall Architecture Review", "cwfix_pdf_001 appears in this page."])
    simple_pdf(c / "pdf" / "运维手册节选.pdf", ["Ops Manual Excerpt", "cwfix_pdf_002 for second pdf."])


def vault() -> None:
    v = ACC / "vault"
    w(v / ".obsidian" / "app.json", '{"tag": "凿壁测试词·藏"}')
    w(v / "笔记A.md", "# 笔记A\n凿壁测试词·林：vault 根目录笔记。\n")
    w(v / "子目录" / "笔记B.md", "# 笔记B\n凿壁测试词·木：vault 子目录笔记。\n")


def single_file() -> None:
    w(ACC / "单文件-项目周报-2024Q4.md", "# 项目周报 2024Q4\n本周完成 M2 原型。凿壁测试词·周。\n")


def excludes() -> None:
    e = ACC / "root" / "excludes"
    for sub in ("single_file", "dir/secret_dir", "glob/drafts"):
        (e / sub).mkdir(parents=True, exist_ok=True)
    import openpyxl as _xl
    wb = _xl.Workbook()
    wb.active.append(["姓名", "工资"])
    wb.active.append(["虚构甲", "凿壁敏感词·薪 12345"])
    wb.save(str(e / "single_file" / "被排除-敏感工资表.xlsx"))
    w(e / "single_file" / "对照-正常文件.md", "# 对照\n凿壁测试词·对照一。\n")
    w(e / "dir" / "secret_dir" / "秘密笔记.md", "# 秘密\n凿壁敏感词·密：整目录排除验证。\n")
    w(e / "dir" / "对照-正常文件.md", "# 对照\n凿壁测试词·对照二。\n")
    w(e / "glob" / "debug-2024.log", "DEBUG 凿壁敏感词·志 in log file.\n")
    w(e / "glob" / "secret-draft.md", "# draft\n凿壁敏感词·草：前缀 glob 排除。\n")
    w(e / "glob" / "drafts" / "草稿1.md", "# 草稿\n凿壁敏感词·稿：递归 glob 排除。\n")
    w(e / "glob" / "临时笔记.tmp", "凿壁敏感词·临：默认 tmp 排除。\n")
    w(e / "glob" / "对照-正常文件.md", "# 对照\n凿壁测试词·对照三。\n")


def edge() -> None:
    e = ACC / "root" / "edge"
    for sub in ("defaults/project/node_modules/lib", "defaults/project/.git", "defaults/project/.svn", "defaults/project/src", "defaults/vault/.obsidian"):
        (e / sub).mkdir(parents=True, exist_ok=True)
    wb(e / "empty.md", b"")
    wb(e / "empty.txt", b"")
    w(e / "gbk.txt", "这是 GBK 编码的中文文档，包含词条「凿壁测试词·编码」。\n", encoding="gbk")
    wb(e / "corrupted.docx", b"this is definitely not a zip archive")
    wb(e / "old_format.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    wb(e / "old_format.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    wb(e / "binary.png", bytes(random.randbytes(1024)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("inner.txt", "zip 内的文本")
    wb(e / "binary.zip", buf.getvalue())
    w(e / "meta.json", '{"marker": "凿壁敏感词·杰森"}')  # 不支持格式 -> 排除轨道（M2-FIX2 后）
    w(e / ".hidden.md", "# 隐藏\n凿壁敏感词·隐。\n")
    w(e / "~$幽灵.docx", "凿壁敏感词·幽：Office 临时文件。\n")
    w(e / "defaults" / "project" / "node_modules" / "lib" / "index.js", "// 凿壁敏感词·模\nmodule.exports = 1;\n")
    w(e / "defaults" / "project" / ".git" / "config", "[core]\n# 凿壁敏感词·git\n")
    w(e / "defaults" / "project" / ".svn" / "entries", "凿壁敏感词·svn\n")
    w(e / "defaults" / "project" / "src" / "正常代码.md", "# 正常\n凿壁测试词·对照四。\n")
    w(e / "defaults" / "vault" / ".obsidian" / "app.json", '{"tag": "凿壁敏感词·藏二"}')


def generated() -> None:
    g = ACC / "root" / "generated"
    # 文本截断上限按字符计（text_cap_chars=5M 字符，UTF-8 中文约 15MB 字节）。
    # 文件名 big_over_cap.md（清单 AC-01-19 同步引用）：5M 字符边界前放 A、之后放 B。
    fill_unit = "填" * 2000 + "\n\n"          # 2002 字符/段
    head = "# 大文件\n截断前标记 cwfix_big_a 在文件前部。\n\n"
    n_mid = 5_050_000 // len(fill_unit) + 1     # 前部超过 5M 字符
    tail = "截断后标记 cwfix_big_b 在文件后部，应不命中。\n\n" + fill_unit * 80
    w(g / "big_over_cap.md", head + fill_unit * n_mid + tail)
    wb(g / "big_binary_21mb.bin", bytes(random.randbytes(21 * 1024 * 1024)))


def verify() -> None:
    """生成后自检：关键文件存在 + 标记词可读回。"""
    import docx as _docx
    import openpyxl as _xl
    from pypdf import PdfReader
    checks = []
    c = ACC / "root" / "corpus"
    checks.append(("md 标记词", "凿壁测试词·风" in (c / "md" / "数据库优化笔记.md").read_text(encoding="utf-8")))
    checks.append(("gbk 可解", "凿壁测试词·编码" in (ACC / "root" / "edge" / "gbk.txt").read_text(encoding="gbk")))
    checks.append(("docx 标记词", "凿壁测试词·雪" in "\n".join(p.text for p in _docx.Document(str(c / "docx" / "接口设计说明.docx")).paragraphs)))
    wb = _xl.load_workbook(str(c / "xlsx" / "环境配置清单.xlsx"), read_only=True)
    checks.append(("xlsx sheet 数", len(wb.worksheets) == 2))
    wb.close()
    r = PdfReader(str(c / "pdf" / "架构评审纪要.pdf"))
    checks.append(("pdf 文本层", "cwfix_pdf_001" in (r.pages[0].extract_text() or "")))
    r2 = PdfReader(str(ACC / "root" / "edge" / "encrypted.pdf"))
    checks.append(("pdf 加密", r2.is_encrypted))
    r3 = PdfReader(str(ACC / "root" / "edge" / "scanned_no_text.pdf"))
    checks.append(("pdf 无文本层", not (r3.pages[0].extract_text() or "").strip()))
    big = ACC / "root" / "generated" / "big_over_cap.md"
    big_text = big.read_text(encoding="utf-8")
    checks.append(("big>5M字符", len(big_text) > 5_050_000))
    checks.append(("A在前B在后", big_text.index("cwfix_big_a") < 5_000_000 < big_text.index("cwfix_big_b")))
    checks.append(("21mb bin", (ACC / "root" / "generated" / "big_binary_21mb.bin").stat().st_size == 21 * 1024 * 1024))
    for name, ok in checks:
        print(("OK  " if ok else "FAIL") + " " + name)
    if not all(ok for _n, ok in checks):
        sys.exit(1)


def main() -> None:
    print("生成验收语料 ->", ACC)
    corpus()
    vault()
    single_file()
    excludes()
    edge()
    make_encrypted_pdf()
    make_blank_pdf()
    generated()
    verify()
    print("完成")


if __name__ == "__main__":
    main()
