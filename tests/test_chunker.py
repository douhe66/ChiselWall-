"""Chunk 切分单测（设计 §5）：500-800 字、段落/句子边界、50 字重叠、上限防异常。

运行：python -m unittest tests.test_chunker
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.chunker import Chunker


class ChunkerTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(Chunker().split("").chunks, [])
        self.assertEqual(Chunker().split("   \n  ").chunks, [])

    def test_short_text(self) -> None:
        r = Chunker().split("短文本")
        self.assertEqual(r.chunks, ["短文本"])

    def test_paragraph_boundaries_sizes_overlap(self) -> None:
        # 10 段 x 140 字 = 1400 字 → 预期 2 块（704 + 755），块间 50 字重叠
        p = "这是一段用于测试的中文段落。" * 10
        text = "\n\n".join([p] * 10)
        r = Chunker().split(text)
        self.assertFalse(r.truncated)
        self.assertEqual(len(r.chunks), 2)
        self.assertLessEqual(len(r.chunks[0]), 800)
        self.assertLessEqual(len(r.chunks[1]), 800)
        self.assertGreaterEqual(len(r.chunks[0]), 500)  # min 软约束（段落边界优先）
        # 重叠：后块以「前块末尾 50 字」开头
        self.assertTrue(r.chunks[1].startswith(r.chunks[0][-50:]))

    def test_oversized_paragraph_hard_split(self) -> None:
        # 无标点长段：句子切不开 → 按 max 硬切（5000/800 → 7 块）
        r = Chunker().split("甲" * 5000)
        self.assertEqual(len(r.chunks), 7)
        for c in r.chunks[:-1]:
            self.assertEqual(len(c), 800)
        self.assertEqual(len(r.chunks[-1]), 200)

    def test_sentence_split(self) -> None:
        # 单段 1010 字、每句 101 字以「。」结尾 → 按句切、块 ≤800、句尾完整
        s = "句子内容" * 20 + "。"
        r = Chunker().split(s * 10)
        self.assertEqual(len(r.chunks), 2)
        for c in r.chunks:
            self.assertLessEqual(len(c), 800)
        self.assertTrue(r.chunks[0].endswith("。"))
        self.assertTrue(r.chunks[1].startswith(r.chunks[0][-50:]))

    def test_max_chunks_cap(self) -> None:
        text = "\n\n".join(["段落内容" * 20] * 200)
        r = Chunker(max_chunks=5).split(text)
        self.assertTrue(r.truncated)
        self.assertLessEqual(len(r.chunks), 5)

    def test_custom_sizes(self) -> None:
        c = Chunker(min_chars=100, max_chars=200, overlap=20)
        text = "\n\n".join(["短段" * 15] * 10)
        r = c.split(text)
        self.assertGreater(len(r.chunks), 1)
        for chunk in r.chunks:
            self.assertLessEqual(len(chunk), 200)

    def test_overlap_dropped_when_exceeds_max(self) -> None:
        # 重叠前缀 + 新段超 max 时丢弃重叠，保证 max 硬约束
        c = Chunker(min_chars=50, max_chars=200, overlap=40)
        r = c.split("段" * 190 + "\n\n" + "段" * 190)
        for chunk in r.chunks:
            self.assertLessEqual(len(chunk), 200)

    def test_no_trailing_overlap_garbage(self) -> None:
        # 中文多段：块内容均为原文子串拼接，不产生空白块
        text = "\n\n".join("第{}段内容测试".format(i) * 30 for i in range(12))
        r = Chunker().split(text)
        for c in r.chunks:
            self.assertTrue(c.strip())


if __name__ == "__main__":
    unittest.main()
