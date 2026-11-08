"""Chunk 切分（设计 §5、调研 02 §5.3）。

策略：
    1. 先按段落边界（空行）切分；超大段落按句子（。！？!?；;）再切；
    2. 目标块长 min~max 字（默认 500~800）；段落边界优先，min 为软约束
       （段落过大/过小时允许超界，max 为硬约束）；
    3. 相邻 chunk 前部带上一块末尾 overlap 字（默认 50，设计口径「前块重叠」）；
    4. 单文档 chunk 数上限 max_chunks（默认 2000）防异常文件，超出截断并标记；
    5. 句子都无法切开的长句按 max 硬切。

字符计数以 Python 字符（中文/英文/标点均计 1）为单位。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])")


@dataclass
class ChunkResult:
    chunks: list[str]
    truncated: bool = False  # 超出 chunk 数上限而被截断


class Chunker:
    def __init__(
        self,
        min_chars: int = 500,
        max_chars: int = 800,
        overlap: int = 50,
        max_chunks: int = 2000,
    ) -> None:
        assert 0 < min_chars <= max_chars
        assert 0 <= overlap < min_chars
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.overlap = overlap
        self.max_chunks = max_chunks

    def split(self, text: str) -> ChunkResult:
        text = text.strip()
        if not text:
            return ChunkResult([])
        units = self._units(text)
        chunks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        truncated = False
        for u in units:
            if not cur:
                cur = [u]
                cur_len = len(u)
                continue
            if cur_len + 1 + len(u) <= self.max_chars:
                cur.append(u)
                cur_len += 1 + len(u)
            else:
                # 当前块收口（不足 min 也收：段落边界优先于 min 软约束）
                chunks.append("\n".join(cur))
                tail = chunks[-1][-self.overlap:] if self.overlap > 0 else ""
                if tail and len(tail) + 1 + len(u) <= self.max_chars:
                    cur = [tail, u]
                    cur_len = len(tail) + 1 + len(u)
                else:
                    cur = [u]
                    cur_len = len(u)
            if cur_len >= self.max_chars:
                chunks.append("\n".join(cur))
                cur = []
                cur_len = 0
            if len(chunks) >= self.max_chunks:
                truncated = True
                break
        if cur and not truncated:
            chunks.append("\n".join(cur))
        if len(chunks) > self.max_chunks:
            truncated = True
            chunks = chunks[: self.max_chunks]
        return ChunkResult(chunks, truncated)

    # ---- 内部 ----
    def _units(self, text: str) -> list[str]:
        units: list[str] = []
        for para in _PARAGRAPH_RE.split(text):
            para = para.strip()
            if not para:
                continue
            if len(para) <= self.max_chars:
                units.append(para)
            else:
                units.extend(self._split_long_para(para))
        return units

    def _split_long_para(self, para: str) -> list[str]:
        """超大段落：按句子边界切；句子仍超长则按 max 硬切。"""
        units: list[str] = []
        for piece in _SENTENCE_RE.split(para):
            piece = piece.strip()
            if not piece:
                continue
            if len(piece) <= self.max_chars:
                units.append(piece)
            else:
                units.extend(self._hard_pieces(piece))
        return units

    def _hard_pieces(self, s: str) -> list[str]:
        return [s[i : i + self.max_chars] for i in range(0, len(s), self.max_chars)]
