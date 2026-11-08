"""SiyuanConnector 单测（M3-1，设计 §6 / 调研 03 §1）：

.sy=Lute AST JSON 解析（Type 判块、NodeText/NodeTextMark 取文本、Spec 2/4 兼容、
base64/码点约定、Properties.title 作标题、未知节点递归兼容、BOM 容忍）、
工作区校验与目录排除（assets/.siyuan）、加密笔记本密文跳过并记录、失败隔离、
端到端索引检索。样例全部自建虚构（tests/fixtures/siyuan-workspace）。
运行：python -m unittest tests.test_siyuan
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.base import ParseError
from connectors.excludes import ExclusionEngine
from connectors.siyuan import (
    SiyuanConnector,
    node_text,
    parse_sy_file,
    sy_doc_text,
    sy_doc_title,
)
from indexer.db import IndexDB
from indexer.indexer import Indexer
from indexer.jieba_util import build_phrase_match, query_tokens

WS = Path(__file__).resolve().parent / "fixtures" / "siyuan-workspace"


def phrase(q: str) -> str:
    return build_phrase_match(query_tokens(q))


class ParserTest(unittest.TestCase):
    """纯 Python 树遍历解析正确性。"""

    def setUp(self) -> None:
        self.doc = parse_sy_file(WS / "data" / "20210808180117-czj9bvb" / "20240201120000-doc001.sy")
        self.assertIsNotNone(self.doc)

    def test_title_from_properties(self) -> None:
        self.assertEqual(sy_doc_title(self.doc), "思源排版元素测试")

    def test_title_in_searchable_text(self) -> None:
        # 标题并入正文首行（搜标题词可命中文档）
        self.assertTrue(sy_doc_text(self.doc).startswith("# 思源排版元素测试"))

    def test_inline_text_and_marks(self) -> None:
        text = sy_doc_text(self.doc)
        self.assertIn("思源测试词条", text)
        self.assertIn("加粗文本", text)      # NodeTextMark strong
        self.assertIn("链接文本", text)      # NodeTextMark a

    def test_heading_prefix(self) -> None:
        self.assertIn("## 列表小节", sy_doc_text(self.doc))

    def test_lists(self) -> None:
        text = sy_doc_text(self.doc)
        self.assertIn("* 无序列表词条甲", text)      # BulletChar=42 码点
        self.assertIn("1. 有序列表词条一", text)      # Typ=1 有序编号
        self.assertIn("- [ ] 任务列表词条", text)     # Typ=3 任务列表

    def test_code_block_base64_lang(self) -> None:
        text = sy_doc_text(self.doc)
        self.assertIn("python", text)                  # CodeBlockInfo "cHl0aG9u" 解码
        self.assertIn("思源代码块词条", text)

    def test_blockquote(self) -> None:
        self.assertIn("> 引用块词条", sy_doc_text(self.doc))

    def test_image_alt(self) -> None:
        self.assertIn("架构图-图片词条", sy_doc_text(self.doc))

    def test_unknown_type_children_still_extracted(self) -> None:
        # Spec 演进：未知 Type 跳过自身、仍递归 Children
        self.assertIn("未知节点兼容词条", sy_doc_text(self.doc))

    def test_spec4_table(self) -> None:
        doc2 = parse_sy_file(WS / "data" / "20210808180117-czj9bvb" / "20240201120001-doc002.sy")
        self.assertIsNotNone(doc2)
        text = sy_doc_text(doc2)
        self.assertIn("表格词条甲", text)
        self.assertIn("表格词条乙", text)

    def test_empty_children_ok(self) -> None:
        self.assertEqual(node_text({"Type": "NodeDocument"}), "")
        self.assertEqual(node_text({"Type": "NodeFutureType"}), "")

    def test_empty_data_node(self) -> None:
        # 对照真实样本要点：NodeText 的 Data 可省略（omitempty）
        doc3 = parse_sy_file(WS / "data" / "20210808180117-czj9bvb" / "子目录" / "20240201120002-doc003.sy")
        self.assertIsNotNone(doc3)
        text = sy_doc_text(doc3)
        self.assertIn("空Data节点词条", text)
        self.assertEqual(sy_doc_title(doc3), "嵌套子目录文档")  # Properties 缺 updated 仍取到 title

    def test_bom_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bom.sy"
            p.write_bytes(b"\xef\xbb\xbf" + json.dumps(
                {"Type": "NodeDocument", "Children": [{"Type": "NodeParagraph", "Children": [{"Type": "NodeText", "Data": "BOM词条"}]}]}
            ).encode("utf-8"))
            self.assertIn("BOM词条", sy_doc_text(parse_sy_file(p)))

    def test_gibberish_returns_none(self) -> None:
        self.assertIsNone(parse_sy_file(WS / "data" / "20210808180117-czj9bvb" / "加密笔记本样本.sy"))

    def test_non_dict_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "list.sy"
            p.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertIsNone(parse_sy_file(p))


class ConnectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = SiyuanConnector(WS, engine=ExclusionEngine())

    def test_valid_workspace(self) -> None:
        self.assertTrue(self.conn.is_valid_workspace())
        self.assertEqual(self.conn.kind, "siyuan")
        self.assertEqual(self.conn.formats(), frozenset({"sy"}))

    def test_invalid_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bad = SiyuanConnector(Path(td), engine=ExclusionEngine())
            self.assertFalse(bad.is_valid_workspace())
            self.assertEqual(list(bad.list_docs()), [])

    def test_conf_without_kernel_version_still_valid(self) -> None:
        # 真实数据验证结论：用户工作区 conf/conf.json 可解析但无 kernelVersion
        # （该键已迁至 temp/siyuan.db）——须仍判定为合法工作区
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "conf").mkdir()
            (root / "conf" / "conf.json").write_text(
                json.dumps({"lang": "zh_CN", "appearance": {}}), encoding="utf-8"
            )
            conn = SiyuanConnector(root, engine=ExclusionEngine())
            self.assertTrue(conn.is_valid_workspace())

    def test_list_docs(self) -> None:
        rels = {m.rel_path for m in self.conn.list_docs()}
        base = "data/20210808180117-czj9bvb"
        self.assertIn(base + "/20240201120000-doc001.sy", rels)
        self.assertIn(base + "/20240201120001-doc002.sy", rels)
        self.assertIn(base + "/子目录/20240201120002-doc003.sy", rels)
        self.assertIn(base + "/加密笔记本样本.sy", rels)      # 候选（读阶段记失败）
        self.assertNotIn(base + "/assets/资产.sy", rels)      # assets 排除
        self.assertNotIn(base + "/.siyuan/conf.json", rels)   # 隐藏配置排除

    def test_titles(self) -> None:
        metas = {m.rel_path: m for m in self.conn.list_docs()}
        self.assertEqual(metas["data/20210808180117-czj9bvb/20240201120000-doc001.sy"].title, "思源排版元素测试")
        # 加密样本解析失败：标题回退文件名主干
        self.assertEqual(metas["data/20210808180117-czj9bvb/加密笔记本样本.sy"].title, "加密笔记本样本")

    def test_read_doc(self) -> None:
        doc = self.conn.read_doc("data/20210808180117-czj9bvb/20240201120000-doc001.sy")
        self.assertIn("思源测试词条", doc.text)
        self.assertEqual(doc.meta.title, "思源排版元素测试")

    def test_encrypted_read_error(self) -> None:
        # 加密笔记本磁盘密文：跳过并记录（ParseError -> 失败列表）
        with self.assertRaises(ParseError) as cm:
            self.conn.read_doc("data/20210808180117-czj9bvb/加密笔记本样本.sy")
        self.assertEqual(cm.exception.code, "encrypted_or_corrupt")

    def test_readonly(self) -> None:
        p = WS / "data" / "20210808180117-czj9bvb" / "20240201120000-doc001.sy"
        before = p.read_bytes()
        self.conn.read_doc("data/20210808180117-czj9bvb/20240201120000-doc001.sy")
        self.assertEqual(p.read_bytes(), before)


class IndexSearchTest(unittest.TestCase):
    """端到端：挂载工作空间 -> 扫描 -> FTS5 命中 + 失败隔离。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "ws"
        shutil.copytree(WS, self.root)
        self.db = IndexDB(base / "s.db")
        self.conn = SiyuanConnector(self.root, engine=ExclusionEngine())
        self.indexer = Indexer(self.db)
        self.mid = self.db.add_mount("siyuan", str(self.root))
        self.indexer.scan_mount(self.mid, self.conn)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _fts(self, q: str) -> int:
        rows = self.db.query(
            "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("content_jieba : " + phrase(q),),
        )
        return int(rows[0]["n"])

    def test_scan_statuses(self) -> None:
        s = self.db.stats()
        self.assertEqual(s["indexed"], 3)   # doc001/doc002/doc003
        self.assertEqual(s["error"], 1)     # 加密笔记本样本
        self.assertEqual(s["pending"], 0)

    def test_search_hits(self) -> None:
        self.assertGreaterEqual(self._fts("思源测试词条"), 1)
        self.assertGreaterEqual(self._fts("表格词条"), 1)
        self.assertGreaterEqual(self._fts("嵌套子目录词条"), 1)
        self.assertGreaterEqual(self._fts("思源排版元素测试"), 1)  # 搜标题命中

    def test_encrypted_not_searchable(self) -> None:
        doc = self.db.get_doc(self.mid, "data/20210808180117-czj9bvb/加密笔记本样本.sy")
        self.assertEqual(doc.status, "error")
        self.assertEqual(len(self.db.get_chunk_texts(doc.doc_id)), 0)
        failed = self.db.failed_docs()
        self.assertTrue(any("加密笔记本样本.sy" in f["rel_path"] for f in failed))


if __name__ == "__main__":
    unittest.main()
