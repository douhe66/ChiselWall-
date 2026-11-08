"""元宝对话 JSON 连接器单测（M2.5；对应 reviewer YB 用例）：

结构识别（convs+speaker）、title 作标题、convs 按序拼接、
speech 缺失回退 speechesV2 content 的 text msg、双缺跳过、
md 孪生去重（只索引 .md，json 记 duplicate_md_twin）、
非元宝 .json 维持不支持格式排除轨道、坏 JSON 隔离、检索命中（搜 cgroup）。

样例全部为自建虚构数据（tests/fixtures，隐私红线：不复制用户真实笔记）。
运行：python -m unittest tests.test_yuanbao
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
from connectors.directory import DirectoryConnector
from connectors.excludes import ExclusionEngine
from connectors.yuanbao import conv_text, doc_text, parse_yuanbao
from indexer.db import IndexDB
from indexer.indexer import Indexer
from indexer.jieba_util import build_phrase_match, query_tokens

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notes"


def phrase(q: str) -> str:
    return build_phrase_match(query_tokens(q))


class ParseTest(unittest.TestCase):
    """结构识别：convs 数组 + 每项带 speaker 才算元宝导出。"""

    def test_recognize_yuanbao(self) -> None:
        data = parse_yuanbao(FIXTURES / "单独元宝.json")
        self.assertIsNotNone(data)
        self.assertEqual(data["title"], "cgroup 内存限制排查")

    def test_non_yuanbao_dict_none(self) -> None:
        self.assertIsNone(parse_yuanbao(FIXTURES / "配置.json"))

    def test_invalid_json_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(parse_yuanbao(p))

    def test_list_top_level_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "list.json"
            p.write_text("[1, 2]", encoding="utf-8")
            self.assertIsNone(parse_yuanbao(p))

    def test_convs_without_speaker_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nospeaker.json"
            p.write_text(json.dumps({"convs": [{"x": 1}]}), encoding="utf-8")
            self.assertIsNone(parse_yuanbao(p))

    def test_empty_convs_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "empty.json"
            p.write_text(json.dumps({"title": "t", "convs": []}), encoding="utf-8")
            self.assertIsNotNone(parse_yuanbao(p))


class ConvTextTest(unittest.TestCase):
    def test_speech(self) -> None:
        self.assertEqual(conv_text({"speaker": "ai", "speech": "你好"}), "ai: 你好")

    def test_speeches_v2_fallback(self) -> None:
        conv = {
            "speaker": "ai",
            "speechesV2": [
                {"content": [{"type": "text", "msg": "A"}, {"type": "image", "msg": "x"},
                             {"type": "text", "msg": "B"}]}
            ],
        }
        self.assertEqual(conv_text(conv), "ai: A\nB")

    def test_speech_empty_falls_back(self) -> None:
        conv = {"speaker": "ai", "speech": "", "speechesV2": [{"content": [{"type": "text", "msg": "C"}]}]}
        self.assertEqual(conv_text(conv), "ai: C")

    def test_both_missing_skipped(self) -> None:
        self.assertEqual(conv_text({"speaker": "ai"}), "")


class DocTextTest(unittest.TestCase):
    def test_title_and_convs_in_order(self) -> None:
        data = {
            "title": "T",
            "convs": [
                {"speaker": "human", "speech": "Q"},
                {"speaker": "ai", "speech": "A"},
            ],
        }
        self.assertEqual(doc_text(data), "# T\n\nhuman: Q\n\nai: A")


class ConnectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = DirectoryConnector(FIXTURES, engine=ExclusionEngine())

    def test_alone_json_indexed_with_title(self) -> None:
        metas = {m.rel_path: m for m in self.conn.list_docs()}
        self.assertIn("单独元宝.json", metas)
        self.assertFalse(metas["单独元宝.json"].excluded)
        self.assertEqual(metas["单独元宝.json"].title, "cgroup 内存限制排查")  # title 作标题

    def test_md_twin_dedup(self) -> None:
        metas = {m.rel_path: m for m in self.conn.list_docs()}
        self.assertFalse(metas["元宝对话样例.md"].excluded)
        self.assertTrue(metas["元宝对话样例.json"].excluded)
        self.assertIn("duplicate_md_twin", metas["元宝对话样例.json"].exclude_reason)

    def test_non_yuanbao_excluded(self) -> None:
        metas = {m.rel_path: m for m in self.conn.list_docs()}
        self.assertTrue(metas["配置.json"].excluded)
        self.assertIn("不支持的格式", metas["配置.json"].exclude_reason)

    def test_read_alone_json(self) -> None:
        doc = self.conn.read_doc("单独元宝.json")
        self.assertIn("# cgroup 内存限制排查", doc.text)      # title 行
        self.assertIn("单独元宝词条", doc.text)
        self.assertIn("还可关注 memory.oom_control", doc.text)  # speechesV2 回退
        self.assertIn("cgroup v2 路径", doc.text)

    def test_read_twin_json_refused(self) -> None:
        # 红线⑤：孪生 json 被排除，全文获取也不可得
        with self.assertRaises(ParseError) as cm:
            self.conn.read_doc("元宝对话样例.json")
        self.assertEqual(cm.exception.code, "excluded")


class IndexSearchTest(unittest.TestCase):
    """端到端：扫描 -> FTS5 命中（对应验收「搜 cgroup 应命中」）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "notes"
        shutil.copytree(FIXTURES, self.root)
        self.db = IndexDB(base / "d.db")
        self.conn = DirectoryConnector(self.root, engine=ExclusionEngine())
        self.indexer = Indexer(self.db)
        self.mid = self.db.add_mount("directory", str(self.root))
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

    def test_search_cgroup_hits_alone_json(self) -> None:
        self.assertGreaterEqual(self._fts("cgroup"), 1)
        doc = self.db.get_doc(self.mid, "单独元宝.json")
        self.assertEqual(doc.status, "indexed")
        self.assertEqual(doc.title, "cgroup 内存限制排查")
        self.assertGreaterEqual(len(self.db.get_chunk_texts(doc.doc_id)), 1)

    def test_md_twin_json_not_searchable(self) -> None:
        doc = self.db.get_doc(self.mid, "元宝对话样例.json")
        self.assertEqual(doc.status, "excluded")
        self.assertEqual(len(self.db.get_chunk_texts(doc.doc_id)), 0)
        # 孪生内容经 .md 可搜（json 侧零 chunk）
        self.assertGreaterEqual(self._fts("孪生md词条"), 1)


if __name__ == "__main__":
    unittest.main()
