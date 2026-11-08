"""连接器单测（设计 §6）：目录遍历、各格式解析、编码探测、失败隔离、红线。

先运行 tests/make_fixtures.py 生成测试数据。
运行：python -m unittest tests.test_connectors 或 python tests/test_connectors.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.base import SUPPORTED_EXTS, ParseError
from connectors.directory import DirectoryConnector, strip_frontmatter
from connectors.obsidian import ObsidianConnector

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notes"


class ListDocsTest(unittest.TestCase):
    """目录遍历：支持格式列出、被排除项带原因列出、排除目录整棵剪枝。"""

    def setUp(self) -> None:
        self.conn = DirectoryConnector(FIXTURES)

    def test_supported_listed(self) -> None:
        rels = {m.rel_path for m in self.conn.list_docs() if not m.excluded}
        for f in (
            "中文笔记.md",
            "english.md",
            "note.txt",
            "table.csv",
            "report.docx",
            "data.xlsx",
            "paper.pdf",
            "老文档.gbk.txt",
            "subdir/inner.md",
            "大文件.txt",
            "单独元宝.json",
            "元宝对话样例.md",
        ):
            self.assertIn(f, rels)

    def test_excluded_listed_with_reason(self) -> None:
        metas = {m.rel_path: m for m in self.conn.list_docs()}
        for f in (".hidden.md", "~$temp.md", "temp.tmp", "binary.bin", "旧格式.doc", "零字节.txt", "app.log", "配置.json", "元宝对话样例.json"):
            self.assertIn(f, metas)
            self.assertTrue(metas[f].excluded, f)
            self.assertTrue(metas[f].exclude_reason, f)

    def test_pruned_dirs_not_listed(self) -> None:
        # 红线⑤：排除目录整棵不可见（不产出任何文档）
        rels = {m.rel_path for m in self.conn.list_docs()}
        for f in (".git/config.txt", "node_modules/pkg/index.js", ".obsidian/workspace.json"):
            self.assertNotIn(f, rels)

    def test_unsupported_ext_enters_exclusion_track(self) -> None:
        # M2-FIX2：不支持的格式（.json/.log）由静默跳过改为进排除轨道
        metas = {m.rel_path: m for m in self.conn.list_docs()}
        for f in ("app.log", "配置.json"):
            self.assertIn(f, metas)
            self.assertTrue(metas[f].excluded, f)
            self.assertIn("不支持的格式", metas[f].exclude_reason, f)

    def test_rel_path_normalized(self) -> None:
        rels = {m.rel_path for m in self.conn.list_docs()}
        self.assertIn("subdir/inner.md", rels)  # 统一 '/' 分隔


class ReadTest(unittest.TestCase):
    """各格式解析与编码探测。"""

    def setUp(self) -> None:
        self.conn = DirectoryConnector(FIXTURES)

    def test_md_chinese(self) -> None:
        doc = self.conn.read_doc("中文笔记.md")
        self.assertIn("凿壁测试词条", doc.text)
        self.assertNotIn("tags:", doc.text)  # frontmatter 已剥离

    def test_txt_utf8(self) -> None:
        self.assertIn("utf8词条测试", self.conn.read_doc("note.txt").text)

    def test_txt_gbk(self) -> None:
        self.assertIn("gbk词条测试", self.conn.read_doc("老文档.gbk.txt").text)

    def test_docx(self) -> None:
        doc = self.conn.read_doc("report.docx")
        self.assertIn("docx词条测试", doc.text)
        self.assertIn("表格单元A", doc.text)

    def test_xlsx(self) -> None:
        doc = self.conn.read_doc("data.xlsx")
        self.assertIn("xlsx词条测试", doc.text)
        self.assertIn("项目清单", doc.text)

    def test_pdf(self) -> None:
        doc = self.conn.read_doc("paper.pdf")
        self.assertIn("pdfwordterm", doc.text)

    def test_csv(self) -> None:
        doc = self.conn.read_doc("table.csv")
        self.assertIn("凿壁", doc.text)

    def test_truncation_cap(self) -> None:
        conn = DirectoryConnector(FIXTURES, text_cap_chars=2000)
        doc = conn.read_doc("大文件.txt")
        self.assertTrue(doc.truncated)
        self.assertEqual(len(doc.text), 2000)

    def test_corrupt_docx_isolated(self) -> None:
        # 红线④：损坏文件 → ParseError，不抛库原始异常
        with self.assertRaises(ParseError):
            self.conn.read_doc("corrupt.docx")

    def test_legacy_doc_refused(self) -> None:
        with self.assertRaises(ParseError):
            self.conn.read_doc("旧格式.doc")

    def test_excluded_read_refused(self) -> None:
        # 红线⑤：排除的文件全文获取也不可得
        with self.assertRaises(ParseError) as cm:
            self.conn.read_doc(".hidden.md")
        self.assertEqual(cm.exception.code, "excluded")

    def test_missing_file(self) -> None:
        with self.assertRaises(ParseError) as cm:
            self.conn.read_doc("不存在的文件.md")
        self.assertEqual(cm.exception.code, "missing")

    def test_unsupported_ext_refused(self) -> None:
        with self.assertRaises(ParseError) as cm:
            self.conn.read_doc("binary.bin")
        self.assertEqual(cm.exception.code, "excluded")


class RedlineTest(unittest.TestCase):
    """硬性红线：只读、路径穿越防护、单文件挂载。"""

    def test_readonly(self) -> None:
        p = FIXTURES / "note.txt"
        before = p.read_bytes()
        DirectoryConnector(FIXTURES).read_doc("note.txt")
        self.assertEqual(p.read_bytes(), before)

    def test_path_traversal_refused(self) -> None:
        conn = DirectoryConnector(FIXTURES)
        with self.assertRaises(ParseError) as cm:
            conn.resolve("../outside.md")
        self.assertEqual(cm.exception.code, "path_traversal")

    def test_file_mount(self) -> None:
        conn = DirectoryConnector(FIXTURES / "中文笔记.md")
        metas = list(conn.list_docs())
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0].rel_path, "中文笔记.md")
        self.assertIn("凿壁测试词条", conn.read_doc("中文笔记.md").text)

    def test_formats(self) -> None:
        self.assertEqual(DirectoryConnector(FIXTURES).formats(), SUPPORTED_EXTS)


class FrontmatterTest(unittest.TestCase):
    def test_strip(self) -> None:
        self.assertEqual(strip_frontmatter("---\na: 1\n---\n正文"), "正文")

    def test_no_frontmatter(self) -> None:
        self.assertEqual(strip_frontmatter("正文"), "正文")


class ObsidianTest(unittest.TestCase):
    """ObsidianConnector：复用目录连接器，.obsidian 默认排除。"""

    def test_kind_and_formats(self) -> None:
        conn = ObsidianConnector(FIXTURES)
        self.assertEqual(conn.kind, "obsidian")
        self.assertEqual(conn.formats(), SUPPORTED_EXTS)
        self.assertEqual(conn.default_excludes(), [".obsidian/**"])

    def test_obsidian_dir_excluded(self) -> None:
        rels = {m.rel_path for m in ObsidianConnector(FIXTURES).list_docs()}
        self.assertNotIn(".obsidian/workspace.json", rels)

    def test_md_read(self) -> None:
        self.assertIn("凿壁测试词条", ObsidianConnector(FIXTURES).read_doc("中文笔记.md").text)


if __name__ == "__main__":
    unittest.main()
