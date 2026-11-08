"""本地搜索单测（设计 §5/§7；调研 03 §2.3）：路由（jieba 短语/AND/trigram/LIKE）、
topk、按文档去重、自写 snippet 窗口与高亮、排除红线。

先运行 tests/make_fixtures.py。
运行：python -m unittest tests.test_searcher
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.directory import DirectoryConnector
from connectors.excludes import ExcludeRule, ExclusionEngine
from indexer.db import IndexDB
from indexer.indexer import Indexer
from searcher.search import Searcher

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notes"


class SearcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "notes"
        shutil.copytree(FIXTURES, self.root)
        self.db = IndexDB(base / "s.db")
        self.engine = ExclusionEngine()
        self.connector = DirectoryConnector(self.root, engine=self.engine)
        # trigram 兜底路由测试需开启兜底表（F2：默认关闭，测试显式开启）
        self.indexer = Indexer(self.db, trigram_enabled=True)
        self.mount_id = self.db.add_mount("directory", str(self.root))
        self.indexer.scan_mount(self.mount_id, self.connector)
        self.searcher = Searcher(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _paths(self, hits) -> set:
        return {h.rel_path for h in hits}

    # ---- 路由 ----
    def test_jieba_phrase_route(self) -> None:
        hits = self.searcher.search("凿壁测试词条", topk=3)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].route, "jieba")
        self.assertIn("中文笔记.md", self._paths(hits))

    def test_and_fallback_route(self) -> None:
        # 「凿壁」与「词条」在原文中不相邻（中间隔「测试」）：短语失败 -> AND 命中
        hits = self.searcher.search("凿壁 词条", topk=5)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].route, "jieba_and")
        self.assertIn("中文笔记.md", self._paths(hits))

    def test_trigram_fallback_route(self) -> None:
        # 「壁测试」是跨分词边界的 3 字子串：jieba 短语/AND 均不中 -> trigram 命中
        hits = self.searcher.search("壁测试", topk=3)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].route, "trigram")
        self.assertIn("中文笔记.md", self._paths(hits))

    def test_like_fallback_route(self) -> None:
        # 「壁测」2 字子串：jieba 不中、trigram 无 3-gram -> 短查询 LIKE 兜底
        hits = self.searcher.search("壁测", topk=3)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].route, "like")
        self.assertIn("中文笔记.md", self._paths(hits))

    def test_ascii_anchor_blocks_chinese_fragments(self) -> None:
        hits = self.searcher.search("zzzz是干嘛的", topk=5)
        self.assertTrue(all("zzzz" in (h.title + h.snippet_text).lower() for h in hits))

    def test_english_hit(self) -> None:
        hits = self.searcher.search("watchdog incremental", topk=3)
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("english.md", self._paths(hits))

    # ---- topk / 去重 / 排序 ----
    def test_dedup_by_doc(self) -> None:
        # 大文件.txt 8 个 chunk 都含「测试」：同一文档只出一条
        hits = self.searcher.search("测试", topk=8)
        self.assertGreater(len(hits), 1)
        self.assertEqual(len({h.doc_id for h in hits}), len(hits))

    def test_topk_limit(self) -> None:
        hits = self.searcher.search("测试", topk=3)
        self.assertLessEqual(len(hits), 3)

    def test_bm25_order(self) -> None:
        hits = self.searcher.search("测试", topk=8)
        self.assertGreater(len(hits), 1)
        for a, b in zip(hits, hits[1:]):
            self.assertLessEqual(a.score, b.score)  # bm25 升序：越小越相关

    def test_empty_query(self) -> None:
        self.assertEqual(self.searcher.search("   "), [])
        self.assertEqual(self.searcher.search(""), [])

    # ---- snippet ----
    def test_snippet_highlight(self) -> None:
        hits = self.searcher.search("凿壁测试词条", topk=1)
        html_snippet = hits[0].snippet_html
        self.assertIn("<em>", html_snippet)
        self.assertIn("凿壁", html_snippet)
        # 高亮内容不含未转义的原生 HTML
        self.assertNotIn("<h", html_snippet.replace("<em>", "").replace("</em>", ""))

    def test_snippet_window_size(self) -> None:
        # 大文件.txt 是单段长文本：窗口 ±150 字 + 边界微调 ≤ 40
        hits = self.searcher.search("截断上限", topk=1)
        self.assertLessEqual(len(hits[0].snippet_text), 2 * 150 + 80)
        self.assertGreater(len(hits[0].snippet_text), 100)
        self.assertIn("…", hits[0].snippet_html)

    # ---- 全文与排除红线 ----
    def test_get_doc_text(self) -> None:
        doc = self.db.get_doc(self.mount_id, "中文笔记.md")
        text = self.searcher.get_doc_text(doc.doc_id)
        self.assertIn("凿壁测试词条", text)

    def test_get_doc_text_excluded_none(self) -> None:
        # 红线⑤：排除后 chunk 摘除，全文不可得
        pattern = str(self.root / "排除我")
        self.db.add_exclude_rule("dir", pattern)
        self.engine.set_rules([ExcludeRule("dir", pattern)])
        self.indexer.scan_mount(self.mount_id, self.connector)
        hits = self.searcher.search("机密词条", topk=5)
        # 红线⑤：被排除目录下的文档不得出现在结果里（其他含该词的文档仍可命中）
        self.assertTrue(all("排除我" not in h.rel_path for h in hits))
        doc = self.db.get_doc(self.mount_id, "排除我/secret.md")
        self.assertIsNone(self.searcher.get_doc_text(doc.doc_id))

    def test_get_doc_text_missing(self) -> None:
        self.assertIsNone(self.searcher.get_doc_text(999999))

class QuestionSearchTest(unittest.TestCase):
    """提问式查询（M3 用户反馈：自然语言提问零命中）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "notes"
        self.root.mkdir()
        (self.root / "fcntl.md").write_text(
            "# fcntl 笔记\n\nfcntl 是一个系统调用，用于文件描述符控制。\n", encoding="utf-8"
        )
        (self.root / "register.md").write_text(
            "# 寄存器\n\n寄存器溢出指结构体过大导致寄存器不足。\n", encoding="utf-8"
        )
        self.db = IndexDB(base / "q.db")
        self.connector = DirectoryConnector(self.root, engine=ExclusionEngine())
        self.indexer = Indexer(self.db)
        self.mount_id = self.db.add_mount("directory", str(self.root))
        self.indexer.scan_mount(self.mount_id, self.connector)
        self.searcher = Searcher(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _paths(self, hits) -> set:
        return {h.rel_path for h in hits}

    def test_question_hits_after_stopword_filter(self) -> None:
        # 「fcntl调用是什么」：问词被过滤 -> AND 召回 fcntl 笔记
        hits = self.searcher.search("fcntl调用是什么", topk=5)
        self.assertIn("fcntl.md", self._paths(hits))
        self.assertIn(hits[0].route, ("jieba", "jieba_and", "jieba_or"))

    def test_question_phrase_variant(self) -> None:
        hits = self.searcher.search("寄存器溢出是什么", topk=5)
        self.assertIn("register.md", self._paths(hits))

    def test_pure_stopwords_no_results(self) -> None:
        self.assertEqual(self.searcher.search("是什么", topk=5), [])

    def test_or_fallback_when_only_some_terms_match(self) -> None:
        # 结构体只在另一篇：AND 失败 -> OR 兜底召回 fcntl.md
        hits = self.searcher.search("fcntl 与 结构体 是什么", topk=5)
        self.assertIn("fcntl.md", self._paths(hits))
        self.assertEqual(hits[0].route, "jieba_or")


if __name__ == "__main__":
    unittest.main()
