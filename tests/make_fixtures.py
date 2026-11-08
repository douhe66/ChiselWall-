"""幂等生成测试数据 tests/fixtures/（设计约定：测试数据自建，勿用真实敏感数据）。

生成内容：
    中文/英文 md（含 frontmatter）、UTF-8 与 GBK txt、csv、
    docx（段落+表格）、xlsx（多 sheet）、最小 PDF（手工构造，ASCII 文本）、
    大文件（截断测试用）、损坏 docx、旧格式 .doc、
    以及应被排除的样本（隐藏文件/.git/node_modules/.obsidian/~$ 临时/.tmp/二进制/空文件）。

用法：python tests/make_fixtures.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "tests" / "fixtures" / "notes"


def w(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def wb(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_texts() -> None:
    w(
        NOTES / "中文笔记.md",
        """---
tags: [测试]
---

# 凿壁测试文档

凿壁测试词条：这是一份用于验证中文全文检索的中文笔记。

## 部门知识共享

部门知识共享的核心思路是每台电脑只索引不复制，中心只做路由。
SQLite FTS5 配合 jieba 分词实现中文检索，本地搜索毫秒级返回。

## 排除规则

排除规则确保隐私：排除即不索引，全文获取也不可得。
隐藏文件、版本库目录与临时文件默认排除。

## 随手记

随手记支持挂载目录与单文件，增量索引保持低资源占用。
""",
    )
    w(
        NOTES / "english.md",
        """# Local Index Notes

ChiselWall local index: each computer indexes its own notes only.
SQLite FTS5 and jieba tokenization power Chinese full text search.
Watchdog incremental indexing with debounce keeps resource usage low.
""",
    )
    w(NOTES / "note.txt", "这是 UTF-8 编码的中文文本文件，包含词条「utf8词条测试」。\n")
    w(
        NOTES / "老文档.gbk.txt",
        "这是 GBK 编码的中文文档，用于测试编码探测，包含词条「gbk词条测试」。\n",
        encoding="gbk",
    )
    w(
        NOTES / "table.csv",
        "名称,数量,备注\n凿壁,1,部门知识共享\n测试条目,3,中文检索\n",
    )
    w(NOTES / "大文件.txt", "这是一段用于测试截断上限的中文内容。" * 300)
    w(NOTES / "subdir" / "inner.md", "# 内层\n子目录里的笔记，包含词条「子目录词条」。\n")
    # 用户排除规则测试样本
    w(NOTES / "排除我" / "secret.md", "# 机密\n用于用户目录排除规则测试，包含词条「机密词条」。\n")
    w(NOTES / "排除我" / "内层" / "秘密2.md", "# 机密2\n目录排除的深层文件，包含词条「机密词条2」。\n")
    w(NOTES / "单独排除.md", "# 单独\n用于用户文件排除规则测试，包含词条「单独排除词条」。\n")
    w(NOTES / "glob_test.md", "# glob\n用于 glob 排除规则测试，包含词条「glob排除词条」。\n")
    # 默认排除样本
    # 不支持的格式（M2-FIX2：进排除轨道而非静默跳过）
    w(NOTES / "app.log", "2024-01-01 10:00:00 INFO 启动完成\n")
    w(NOTES / "配置.json", '{"theme": "dark"}\n')
    # 默认排除样本
    w(NOTES / ".hidden.md", "# 隐藏\n隐藏文件不应被索引。\n")
    w(NOTES / ".git" / "config.txt", "[core]\n")
    w(NOTES / "node_modules" / "pkg" / "index.js", "module.exports = 1;\n")
    w(NOTES / ".obsidian" / "workspace.json", "{}\n")
    w(NOTES / "~$temp.md", "Office 临时文件\n")
    w(NOTES / "temp.tmp", "临时文件\n")
    wb(NOTES / "binary.bin", bytes(random.randbytes(2048)))
    wb(NOTES / "零字节.txt", b"")


def make_docx() -> None:
    import docx

    d = docx.Document()
    d.add_heading("项目周报", level=1)
    d.add_paragraph("Word文档测试内容：本周完成本地索引原型验证。")
    d.add_paragraph("docx词条测试出现在正文段落中。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "表格单元A"
    t.cell(0, 1).text = "表格单元B"
    t.cell(1, 0).text = "状态"
    t.cell(1, 1).text = "已完成"
    d.save(str(NOTES / "report.docx"))


def make_xlsx() -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "员工名单"
    ws.append(["姓名", "部门", "备注"])
    ws.append(["张三", "平台组", "xlsx词条测试"])
    ws2 = wb.create_sheet("项目清单")
    ws2.append(["项目", "状态"])
    ws2.append(["凿壁", "M2 开发中"])
    wb.save(str(NOTES / "data.xlsx"))


def _write_simple_pdf(path: Path, lines: list[str]) -> None:
    """手工构造最小 PDF（Type1 Helvetica、ASCII 文本），避免引入 reportlab 依赖。"""

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
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


def make_pdf() -> None:
    _write_simple_pdf(
        NOTES / "paper.pdf",
        [
            "ChiselWall Local Index Research",
            "PDF text extraction works with pypdf.",
            "pdfwordterm appears in this page.",
        ],
    )


def make_broken() -> None:
    wb(NOTES / "corrupt.docx", b"this is definitely not a zip archive")
    # OLE 复合文档魔数开头、但内容非法的旧格式样本
    wb(NOTES / "旧格式.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)


def make_yuanbao() -> None:
    """元宝对话 JSON 样本（M2.5，虚构数据，非真实对话）。"""
    import json

    # 无 md 孪生：正常索引；覆盖 speech、speechesV2 回退、双缺跳过
    alone = {
        "id": "conv-demo-alone",
        "title": "cgroup 内存限制排查",
        "agentId": "demo",
        "projectId": "proj-x",
        "convs": [
            {"speaker": "human", "speech": "单独元宝词条：容器内 cgroup 内存限制怎么排查？"},
            {"speaker": "ai", "speech": "查看 /sys/fs/cgroup/memory/memory.limit_in_bytes 与 memory.stat。"},
            {"speaker": "human", "speech": "还有别的指标吗？"},
            {
                "speaker": "ai",
                "speechesV2": [
                    {
                        "content": [
                            {"type": "text", "msg": "还可关注 memory.oom_control 与当前用量。"},
                            {"type": "text", "msg": "cgroup v2 路径为 /sys/fs/cgroup/memory.current。"},
                        ]
                    }
                ],
            },
            {"speaker": "ai"},  # speech 与 speechesV2 均缺失：跳过该轮
        ],
    }
    w(NOTES / "单独元宝.json", json.dumps(alone, ensure_ascii=False, indent=2))

    # md 孪生成对：只索引 .md，.json 记 duplicate_md_twin
    twin = {
        "id": "conv-demo-twin",
        "title": "孪生对话",
        "convs": [
            {"speaker": "human", "speech": "这条对话有 md 孪生，json 不应被索引。"},
            {"speaker": "ai", "speech": "好的。"},
        ],
    }
    w(NOTES / "元宝对话样例.json", json.dumps(twin, ensure_ascii=False, indent=2))
    w(NOTES / "元宝对话样例.md", "# 元宝对话样例\n孪生md词条：md 版本被索引。\n")


def make_siyuan() -> None:
    """思源工作空间样本（M3-1，调研 03 §1 官方结构自建，虚构内容）。

    覆盖：Spec 2 普通文档（段落/标题/三种列表/代码块/引用/图片/未知节点兼容）、
    Spec 4 表格、嵌套子目录、加密笔记本密文、assets/.siyuan 排除目录。
    """
    import json

    ws = NOTES.parent / "siyuan-workspace"
    box = ws / "data" / "20210808180117-czj9bvb"
    w(ws / "conf" / "conf.json", json.dumps({"kernelVersion": "3.1.0"}, ensure_ascii=False))

    doc001 = {
        "ID": "20240201120000-doc001",
        "Spec": "2",
        "Type": "NodeDocument",
        "Properties": {
            "id": "20240201120000-doc001",
            "title": "思源排版元素测试",
            "type": "doc",
            "updated": "20240201120000",
        },
        "Children": [
            {
                "Type": "NodeParagraph",
                "Children": [
                    {"Type": "NodeText", "Data": "思源测试词条：这是直接读取 .sy 得到的正文。"},
                    {"Type": "NodeTextMark", "TextMarkType": "strong", "TextMarkTextContent": "加粗文本"},
                    {"Type": "NodeText", "Data": "，以及"},
                    {"Type": "NodeTextMark", "TextMarkType": "a", "TextMarkTextContent": "链接文本", "TextMarkAHref": "https://example.com"},
                    {"Type": "NodeText", "Data": "。"},
                ],
            },
            {
                "Type": "NodeHeading",
                "HeadingLevel": 2,
                "Children": [{"Type": "NodeText", "Data": "列表小节"}],
            },
            {
                "Type": "NodeList",
                "ListData": {"Typ": 0, "Marker": "Kg==", "BulletChar": 42},
                "Children": [
                    {"Type": "NodeListItem", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "无序列表词条甲"}]}]},
                    {"Type": "NodeListItem", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "无序列表词条乙"}]}]},
                ],
            },
            {
                "Type": "NodeList",
                "ListData": {"Typ": 1, "Marker": "MQ=="},
                "Children": [
                    {"Type": "NodeListItem", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "有序列表词条一"}]}]},
                ],
            },
            {
                "Type": "NodeList",
                "ListData": {"Typ": 3},
                "Children": [
                    {"Type": "NodeListItem", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "任务列表词条"}]}]},
                ],
            },
            {
                "Type": "NodeCodeBlock",
                "CodeBlockInfo": "cHl0aG9u",
                "Children": [{"Type": "NodeCodeBlockCode", "Data": "print('思源代码块词条')"}],
            },
            {
                "Type": "NodeBlockquote",
                "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "引用块词条"}]}],
            },
            {"Type": "NodeImage", "Properties": {"title": "架构图-图片词条"}},
            {
                "Type": "NodeFutureFeature",
                "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "未知节点兼容词条"}]}],
            },
        ],
    }
    w(box / "20240201120000-doc001.sy", json.dumps(doc001, ensure_ascii=False, indent=2))

    doc002 = {
        "ID": "20240201120001-doc002",
        "Spec": "4",
        "Type": "NodeDocument",
        "Properties": {
            "id": "20240201120001-doc002",
            "title": "表格测试",
            "type": "doc",
            "updated": "20240201120001",
        },
        "Children": [
            {
                "Type": "NodeTable",
                "Children": [
                    {"Type": "NodeTableCell", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "表格词条甲"}]}]},
                    {"Type": "NodeTableCell", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "表格词条乙"}]}]},
                ],
            }
        ],
    }
    w(box / "20240201120001-doc002.sy", json.dumps(doc002, ensure_ascii=False, indent=2))

    doc003 = {
        "ID": "20240201120002-doc003",
        "Spec": "2",
        "Type": "NodeDocument",
        "Properties": {
            "id": "20240201120002-doc003",
            "title": "嵌套子目录文档",
            "type": "doc",
            # 注意：无 updated 键（对照真实样本「Properties 缺 updated」容错）
        },
        "Children": [
            {
                "Type": "NodeParagraph",
                "Children": [{"Type": "NodeText", "Data": "嵌套子目录词条：子目录下的 .sy 也能索引。"}],
            },
            {
                "Type": "NodeParagraph",
                # 对照真实样本「NodeText 空 Data」：Data 可省略（omitempty），
                # 空 TextMark 不代表空文本
                "Children": [
                    {"Type": "NodeText"},
                    {"Type": "NodeTextMark", "TextMarkType": "text", "TextMarkTextContent": "空Data节点词条"},
                ],
            },
        ],
    }
    w(box / "子目录" / "20240201120002-doc003.sy", json.dumps(doc003, ensure_ascii=False, indent=2))

    # 加密笔记本磁盘密文：非 JSON（跳过并记录，失败列表）
    wb(box / "加密笔记本样本.sy", b"\x89PNG\r\n\x1a\n" + b"siyuan-encrypted-ciphertext-bytes" * 3)
    # 应被排除的目录：assets（资源）与 data/.siyuan（隐藏配置）
    w(box / "assets" / "资产.sy", json.dumps({"Type": "NodeDocument", "Properties": {"title": "资产文档"}}, ensure_ascii=False))
    w(box / ".siyuan" / "conf.json", "{}")


def main() -> None:
    print(f"生成测试数据 -> {NOTES}")
    make_texts()
    make_docx()
    make_xlsx()
    make_pdf()
    make_broken()
    make_yuanbao()
    make_siyuan()
    print("完成")


if __name__ == "__main__":
    sys.exit(main())
