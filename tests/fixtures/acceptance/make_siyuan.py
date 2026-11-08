"""M3 思源 .sy 验收语料生成（reviewer，幂等）。依据 docs/research/_raw/sy-format-zh-cn.md。

生成 tests/fixtures/acceptance/siyuan/：
    workspace/          模拟思源工作空间（挂载 kind=siyuan）：
      data/20250101120000-cwbox/           笔记本目录（box）
        <rootID>.sy                         各结构变体样本（ID=文件名去 .sy）
        assets/资源.png                     资源目录（应不索引）
    独立挂载-单文档.sy                     单文件挂载样本（可选）
    real/                                  用户脱敏真实 workspace 样本位（README 说明）

标记词体系：凿壁思源词·X（每样本一个）。
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

SIY = Path(__file__).resolve().parent / "siyuan"
BOX = SIY / "workspace" / "data" / "20250101120000-cwbox"


def root(title: str, spec: str, children: list) -> dict:
    doc_id = "20250101120000-cw" + ("%04d" % root.seq)
    root.seq += 1
    return {
        "ID": doc_id,
        "Spec": spec,
        "Type": "NodeDocument",
        "Properties": {"icon": "1f4d6", "id": doc_id, "title": title, "type": "doc", "updated": "20250101120000"},
        "Children": children,
    }
root.seq = 1


def bid(n: int) -> str:
    return "20250101120001-cw" + ("%04d" % n)


def para(text: str, n: int) -> dict:
    return {"Type": "NodeParagraph", "ID": bid(n), "Properties": {"id": bid(n), "updated": "20250101120001"},
            "Children": [{"Type": "NodeText", "Data": text}]}


def heading(level: int, text: str, n: int) -> dict:
    return {"Type": "NodeHeading", "ID": bid(n), "HeadingLevel": level,
            "Properties": {"id": bid(n), "updated": "20250101120001"},
            "Children": [{"Type": "NodeText", "Data": text}]}


def write_sy(doc: dict, path: Path, bom: bool = False) -> None:
    raw = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + raw if bom else raw)


def sample_standard() -> dict:
    return root("思源标准文档", "2", [
        heading(2, "标准文档标题", 1),
        para("凿壁思源词·标准：这是标准 Spec 2 文档的正文段落。", 2),
        {"Type": "NodeList", "ID": bid(3), "ListData": {},
         "Properties": {"id": bid(3), "updated": "20250101120001"},
         "Children": [{"Type": "NodeListItem", "ID": bid(4),
                       "ListData": {"BulletChar": 42, "Marker": "Kg=="},
                       "Properties": {"id": bid(4), "updated": "20250101120001"},
                       "Children": [para("无序列表项一", 5)]}]},
        {"Type": "NodeList", "ID": bid(6), "ListData": {"Typ": 1},
         "Properties": {"id": bid(6), "updated": "20250101120001"},
         "Children": [{"Type": "NodeListItem", "ID": bid(7),
                       "ListData": {"Typ": 1, "Tight": True, "Start": 1, "Delimiter": 46, "Padding": 3, "Marker": "MS4=", "Num": 1},
                       "Properties": {"id": bid(7), "updated": "20250101120001"},
                       "Children": [para("有序列表项一", 8)]}]},
        {"Type": "NodeList", "ID": bid(9), "ListData": {"Typ": 3},
         "Properties": {"id": bid(9), "updated": "20250101120001"},
         "Children": [{"Type": "NodeListItem", "ID": bid(10),
                       "ListData": {"Typ": 3, "Tight": True, "BulletChar": 45, "Padding": 2, "Marker": "LQ==", "Num": -1},
                       "Properties": {"id": bid(10), "updated": "20250101120001"},
                       "Children": [{"Type": "NodeTaskListItemMarker", "TaskListItemChecked": True, "TaskListItemMarker": 88},
                                    para("待办任务项", 11)]}]},
        {"Type": "NodeBlockquote", "ID": bid(12), "Properties": {"id": bid(12), "updated": "20250101120001"},
         "Children": [{"Type": "NodeBlockquoteMarker", "Data": "> "}, para("引述内容：部门经验总结", 13)]},
        {"Type": "NodeCodeBlock", "ID": bid(14), "CodeBlockInfo": base64.b64encode(b"python").decode(),
         "Properties": {"id": bid(14), "updated": "20250101120001"},
         "Children": [{"Type": "NodeCodeBlockFenceOpenMarker", "Data": "```"},
                      {"Type": "NodeCodeBlockFenceInfoMarker"},
                      {"Type": "NodeCodeBlockCode", "Data": "print('hello chiselwall')"},
                      {"Type": "NodeCodeBlockFenceCloseMarker", "Data": "```"}]},
        {"Type": "NodeTable", "ID": bid(15), "TableAligns": [0, 0],
         "Properties": {"id": bid(15), "updated": "20250101120001"},
         "Children": [{"Type": "NodeTableHead", "Data": "thead", "Children": [
             {"Type": "NodeTableRow", "Data": "tr", "Children": [
                 {"Type": "NodeTableCell", "Data": "th", "Children": [{"Type": "NodeText", "Data": "表头A"}]},
                 {"Type": "NodeTableCell", "Data": "th", "Children": [{"Type": "NodeText", "Data": "表头B"}]}]}]},
             {"Type": "NodeTableRow", "Data": "tr", "Children": [
                 {"Type": "NodeTableCell", "Data": "td", "Children": [{"Type": "NodeText", "Data": "单元格甲"}]},
                 {"Type": "NodeTableCell", "Data": "td", "Children": [{"Type": "NodeText", "Data": "凿壁思源词·表格"}]}]}]},
        {"Type": "NodeThematicBreak", "ID": bid(16), "Properties": {"id": bid(16), "updated": "20250101120001"}},
        para("文档结尾段。", 17),
    ])


def sample_textmark() -> dict:
    return root("思源行级标记文档", "2", [
        para("凿壁思源词·标记：", 1),
        {"Type": "NodeParagraph", "ID": bid(2), "Properties": {"id": bid(2), "updated": "20250101120001"},
         "Children": [
             {"Type": "NodeText", "Data": "普通文本 "},
             {"Type": "NodeTextMark", "TextMarkType": "strong", "TextMarkTextContent": "加粗词条甲"},
             {"Type": "NodeText", "Data": " "},
             {"Type": "NodeTextMark", "TextMarkType": "em", "TextMarkTextContent": "倾斜词条乙"},
             {"Type": "NodeText", "Data": " "},
             {"Type": "NodeTextMark", "TextMarkType": "code", "TextMarkTextContent": "行内代码丙"},
             {"Type": "NodeText", "Data": " "},
             {"Type": "NodeTextMark", "TextMarkType": "a", "TextMarkAHref": "https://ld246.com", "TextMarkTextContent": "超链接丁"},
             {"Type": "NodeText", "Data": " "},
             {"Type": "NodeTextMark", "TextMarkType": "inline-math", "TextMarkInlineMathContent": "a^2 + b^2 = c^2"},
             {"Type": "NodeText", "Data": " "},
             {"Type": "NodeTextMark", "TextMarkType": "inline-memo", "TextMarkInlineMemoContent": "行级备注内容", "TextMarkTextContent": "备注戊"},
         ]},
        {"Type": "NodeParagraph", "ID": bid(3), "Properties": {"id": bid(3), "updated": "20250101120001"},
         "Children": [{"Type": "NodeTextMark", "Properties": {"style": "color: var(--b3-font-color1);"},
                       "TextMarkType": "strong", "TextMarkTextContent": "带样式文本己"},
                      {"Type": "NodeKramdownSpanIAL", "Data": '{: style="color: var(--b3-font-color1);"}'}]},
        # Data 省略的 NodeText（空文本标记）
        {"Type": "NodeParagraph", "ID": bid(4), "Properties": {"id": bid(4), "updated": "20250101120001"},
         "Children": [{"Type": "NodeText"}, {"Type": "NodeTextMark", "TextMarkType": "mark", "TextMarkTextContent": "高亮庚"}]},
    ])


def sample_spec3() -> dict:
    return root("思源页签文档", "3", [
        para("凿壁思源词·页签：Spec 3 页签文档。", 1),
        {"Type": "NodeTabs", "ID": bid(2), "Properties": {"id": bid(2), "updated": "20250101120001"},
         "Children": [{"Type": "NodeTabItem", "ID": bid(3), "Properties": {"id": bid(3), "updated": "20250101120001"},
                       "Children": [para("页签一内容", 4)]}]},
    ])


def sample_spec4() -> dict:
    return root("思源富文本表格文档", "4", [
        para("凿壁思源词·富表：Spec 4 富文本表格。", 1),
        {"Type": "NodeTable", "ID": bid(2), "TableAligns": [0],
         "Properties": {"id": bid(2), "updated": "20250101120001", "custom-sy-table-rich": "1"},
         "Children": [{"Type": "NodeTableRow", "Data": "tr", "Children": [
             {"Type": "NodeTableCell", "Data": "td", "TableCellRich": {"spec": 1, "format": "kramdown", "content": "- first\n- second"},
              "Children": [{"Type": "NodeText", "Data": "富文本单元格投影"}]}]}]},
    ])


def sample_spec5() -> dict:
    return root("思源思维导图文档", "5", [
        para("凿壁思源词·导图：Spec 5 思维导图。", 1),
        {"Type": "NodeMindmap", "ID": bid(2), "Properties": {"id": bid(2), "updated": "20250101120001"},
         "Children": [{"Type": "NodeMindmapItem", "ID": bid(3), "Properties": {"id": bid(3), "updated": "20250101120001"},
                       "Children": [para("导图节点内容", 4)]}]},
    ])


def sample_spec6() -> dict:
    return root("思源/斜杠标题文档", "6", [
        para("凿壁思源词·斜杠：Spec 6 标题含 ASCII 斜杠。", 1),
    ])


def sample_fake_block() -> dict:
    # Type 判别样本：未知 Type、非块节点带 ID（历史遗留）、按 Type 安全处理
    return root("思源伪块样本", "2", [
        para("凿壁思源词·伪块：", 1),
        {"Type": "NodeUnknownFutureType", "ID": bid(2), "Properties": {"id": bid(2), "updated": "20250101120001"},
         "Children": [{"Type": "NodeText", "Data": "未来节点文本"}]},
        {"Type": "NodeText", "ID": bid(3), "Data": "非块节点带历史ID"},
    ])


def sample_bom() -> dict:
    return root("思源BOM样本", "2", [para("凿壁思源词·编码：BOM 前缀 UTF-8。", 1)])


def write_all() -> None:
    import shutil
    if BOX.exists():
        shutil.rmtree(BOX)
    BOX.mkdir(parents=True)
    # 工作区校验：conf/conf.json 存在且可解析（SiyuanConnector.is_valid_workspace）
    conf = SIY / "workspace" / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "conf.json").write_text('{"kernelVersion": "3.1.0", "lang": "zh_CN"}', encoding="utf-8")
    def sy(doc: dict, bom: bool = False) -> None:
        write_sy(doc, BOX / (doc["ID"] + ".sy"), bom=bom)
    sy(sample_standard())
    sy(sample_textmark())
    sy(sample_spec3())
    sy(sample_spec4())
    sy(sample_spec5())
    sy(sample_spec6())
    sy(sample_fake_block())
    sy(sample_bom(), bom=True)
    # 密文（加密笔记本）：非 JSON 字节
    (BOX / "20250101120009-cw0009.sy").write_bytes(b"SIYUAN-ENCRYPTED-CIPHERTEXT-\x00\x01" * 8)
    # 坏 JSON：截断
    good = json.dumps(sample_standard(), ensure_ascii=False).encode("utf-8")
    (BOX / "20250101120010-cw0010.sy").write_bytes(good[: len(good) // 2])
    # 语法错误 JSON
    (BOX / "20250101120011-cw0011.sy").write_text('{"ID": "x", "Spec": "2", 缺逗号 broken', encoding="utf-8")
    # assets 资源（应不索引）
    assets = BOX / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "资源.png").write_bytes(b"\x89PNG fake resource bytes")
    # 单文件挂载样本（独立于 workspace）
    write_sy(root("独立挂载的思源单文档", "2", [para("凿壁思源词·独立：单文件挂载。", 1)]),
             SIY / "独立挂载-单文档.sy")
    # real/ 说明
    real = SIY / "real"
    real.mkdir(parents=True, exist_ok=True)
    (real / "README.md").write_text(
        "# 真实工作空间样本位\n\n将用户提供的脱敏思源 workspace（data/ 目录，含多笔记本与真实 .sy）放于本目录。\n"
        "SC-01~08 以真实样本重跑为最终判定。\n", encoding="utf-8")


def verify() -> None:
    import json as _j
    checks = []
    std = BOX / "20250101120000-cw0001.sy"
    d = _j.loads(std.read_text(encoding="utf-8"))
    checks.append(("根 Type", d["Type"] == "NodeDocument" and d["Spec"] == "2"))
    checks.append(("标准标记词", "凿壁思源词·标准" in std.read_text(encoding="utf-8")))
    checks.append(("ID=文件名", d["ID"] + ".sy" == std.name))
    tm = BOX / "20250101120000-cw0002.sy"
    checks.append(("TextMark 样本", "TextMarkTextContent" in tm.read_text(encoding="utf-8")))
    bomf = BOX / "20250101120000-cw0008.sy"
    checks.append(("BOM 前缀", bomf.read_bytes().startswith(b"\xef\xbb\xbf")))
    cip = BOX / "20250101120009-cw0009.sy"
    checks.append(("密文非JSON", b"SIYUAN-ENCRYPTED" in cip.read_bytes()))
    bad = BOX / "20250101120010-cw0010.sy"
    try:
        _j.loads(bad.read_text(encoding="utf-8"))
        checks.append(("截断样本", False))
    except Exception:
        checks.append(("截断样本", True))
    checks.append(("assets 存在", (BOX / "assets" / "资源.png").exists()))
    checks.append(("单文件样本", (SIY / "独立挂载-单文档.sy").exists()))
    for name, ok in checks:
        print(("OK  " if ok else "FAIL") + " " + name)
    if not all(ok for _n, ok in checks):
        sys.exit(1)


if __name__ == "__main__":
    print("生成思源 .sy 验收语料 ->", SIY)
    write_all()
    verify()
    print("完成")
