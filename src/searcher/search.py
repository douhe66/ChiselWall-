"""本地搜索（设计 §5/§7/§12；调研 03 §2.3 落地）。

查询路由（P0 jieba 主索引 + P1 trigram 兜底，hermes「零命中才重试」思路）：
    1. jieba 短语：build_phrase_match(tokens) 对 chunks_fts.content_jieba 列
       （content 主列一并 OR，覆盖纯英文/数字）——词序相邻语义；
    2. 短语零命中且 token 数 > 1：降级 AND（各 token 独立短语，词需齐全但可散落）；
    3. 仍零命中：trigram 兜底表——查询整串去空白标点后取 3-gram（去重、限量），
       gram OR 化（dsh matchOf 思路；2 字词由 jieba 列覆盖，<3 字查询见第 4 步）；
    4. 短查询（≤8 字符）最终兜底 LIKE '%q%'（hermes _search 同思路；全表 LIKE 代价高，
       仅 FTS 全零命中且短查询时执行）。
    排序始终 bm25；兜底只影响召回、不影响主索引排名。

提问式查询（用户真实场景「fcntl调用是什么」）：规则式问句解析——
    问词/停用词（是什么/怎么/如何/的/了…）不参与匹配；术语按 AND -> OR+覆盖度召回，
    避免「全匹配」导致自然语言提问零命中（设计 §10 查询增强的规则版；LLM 改写为 M5 可选项）。

snippet 自写（设计 §5、调研 03 §2.3④，不用 FTS5 snippet()）：
    锚定首个命中 token 在 chunk 原文的位置，前后各取 window（默认 150）字符，
    窗口尽量落在句号/换行边界，首尾加省略号；命中 token 以 <em> 包裹
    （服务端先转义再插标签，UI 可直接 v-html）。

预留（§16，只登记不动工）：gram 按 idf 截断（dsh 实测提升，量大时再做）、
向量语义混合检索（vec0）、BM25 跨节点归一化（M4 聚合层）。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from typing import Optional

from indexer.db import IndexDB
from indexer.jieba_util import (
    GENERIC_TERMS,
    build_phrase_match,
    fts_quote,
    query_phrases,
    query_tokens,
    sanitize_token,
)

# 主索引与兜底表名（仅内部常量，拼 SQL 无注入风险）
_FTS_TABLE = "chunks_fts"
_TRIGRAM_TABLE = "chunks_trigram"

# trigram 查询的 gram 数量上限（防超长查询；idf 截断为预留优化）
_MAX_GRAMS = 32
# LIKE 兜底的最大查询长度
_LIKE_MAX_LEN = 8

_TERM_BOUNDARY = re.compile(r"[。！？!?；;\n]")

# 问句停用词（提问式搜索：这些词不参与匹配）
_STOPWORDS = frozenset(
    "什么 是什么 怎么 怎样 如何 为何 为什么 可以 是否 哪些 哪个 哪里 哪些 请问 一下"
    " 有没有 是不是 吗 呢 的 了 是 有 在 和 与 或 我 你 他 它 这 那 个 些 把 被 对 从 到"
    " 能 会 要 想 应该 需要 相关 关于 介绍 解释 说明".split()
)

# 问句标记：只有「提问式输入」才启用停用词过滤与 OR 召回（关键词查询保持精度链路）
_QUESTION_MARKERS = (
    "是什么", "什么", "怎么", "怎样", "如何", "为什么", "为何", "可以", "是否",
    "哪些", "哪个", "哪里", "请问", "有没有", "是不是", "吗", "呢",
)


@dataclass
class SearchHit:
    doc_id: int
    mount_id: int
    title: str
    rel_path: str
    abs_path: str
    format: str
    score: float
    chunk_seq: int
    snippet_html: str
    snippet_text: str
    route: str  # jieba / jieba_and / jieba_or / trigram / like / vector / hybrid
    tier: str = "strong"  # strong | weak（weak 默认折叠、不进 AI 上下文）
    plan_coverage: int = 0
    plan_clause_total: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class Searcher:
    def __init__(
        self,
        db: IndexDB,
        *,
        snippet_window: int = 150,
        vector_index: Optional[object] = None,
        scope: str = "local",
    ) -> None:
        self.db = db
        self.snippet_window = snippet_window
        self.vector_index = vector_index
        self.scope = "remote" if scope == "remote" else "local"

    @property
    def _policy(self) -> str:
        if self.scope == "remote":
            return " AND d.share_state='shared' AND m.enabled=1"
        return " AND d.share_state!='hidden' AND m.enabled=1"

    # ---- 入口 ----
    def search(self, query: str, topk: int = 8) -> list[SearchHit]:
        """topk 检索：按文档去重（同文档多 chunk 只取最高分块）。"""
        raw_tokens = [sanitize_token(t) for t in query_tokens(query)]
        raw_tokens = [t for t in raw_tokens if t]
        if not raw_tokens:
            return []
        question = self._is_question(query)
        terms = self._content_terms(raw_tokens) if question else raw_tokens
        if not terms:
            return []  # 纯问词/停用词，无检索意义
        fetch = min(max(topk * 4, 8), 200)  # 多取再按文档去重
        # 精度优先：多有效词先全词 AND（避免 3-gram 片段劫持）
        sig = [
            t for t in terms
            if (len(t) >= 2 or (t.isascii() and t.isalnum())) and t.lower() not in _STOPWORDS
        ]
        rows: list = []
        route = ""
        multi = len(sig) > 1 and (question or len(query.split()) > 1)
        if multi:
            rows, route = self._match_and(sig, fetch)
        if not rows:
            rows, route = self._match_phrases(query_phrases(query), fetch)
        if not rows and question:
            rows, route = self._match_or_gated(terms, fetch)
        if not rows:
            rows, route = self._match_trigram(query, fetch)
        if not rows:
            rows, route = self._match_like(query, fetch)
        weak = route in ("jieba_or", "trigram", "like")
        hits: list[SearchHit] = []
        seen_docs: set[int] = set()
        for row in rows:
            doc_id = int(row["doc_id"])
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            snippet_html, snippet_text = self._build_snippet(row["text"], terms)
            hits.append(
                SearchHit(
                    doc_id=doc_id,
                    mount_id=int(row["mount_id"]),
                    title=row["title"],
                    rel_path=row["rel_path"],
                    abs_path=row["abs_path"],
                    format=row["format"],
                    score=float(row["score"]),
                    chunk_seq=int(row["seq"]),
                    snippet_html=snippet_html,
                    snippet_text=snippet_text,
                    route=route,
                    tier="weak" if weak else "strong",
                )
            )
            if len(hits) >= (min(topk, 3) if weak else topk):
                break
        return self._anchor_ascii(query, self._merge_vector(query, terms, hits, topk))

    @staticmethod
    def _anchor_ascii(query: str, hits: list[SearchHit]) -> list[SearchHit]:
        """查询含英文/技术术语时，结果必须至少包含其中一个，避免被中文片段带偏。"""
        toks = {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_.+#-]{1,}", query or "")}
        if not toks:
            return hits
        kept = [
            h for h in hits
            if h.route in ("vector", "hybrid")
            or any(t in ((h.title or "") + " " + (h.snippet_text or "")).lower() for t in toks)
        ]
        return kept

    # ---- 向量混合（M7 / P0 噪声收敛） ----
    def _merge_vector(self, query, terms, bm_hits, topk):
        """BM25 + 向量：BM25/混合为主；纯向量命中需过阈值+词面证据，限条且排最后。"""
        vi = self.vector_index
        if vi is None or not getattr(vi, "ready", False):
            return bm_hits
        try:
            vec = vi.search_ids(query, max(topk * 3, 12))
        except Exception:
            return bm_hits
        if not vec:
            return bm_hits
        chunk_ids = [cid for cid, _ in vec]
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.db.query(
            "SELECT c.chunk_id, c.doc_id, c.seq, c.text, d.title, d.rel_path, d.abs_path, d.format, d.mount_id "
            "FROM chunks c JOIN docs d ON d.doc_id=c.doc_id "
            "JOIN mounts m ON m.mount_id=d.mount_id "
            "WHERE d.status='indexed'" + self._policy + " AND c.chunk_id IN (" + placeholders + ")",
            chunk_ids,
        )
        by_id = {int(r["chunk_id"]): r for r in rows}
        min_sim = float(getattr(vi, "min_similarity", 0.62))
        vec_max = int(getattr(vi, "vector_only_max", 3))
        require_lexical = bool(getattr(vi, "require_lexical", True))
        fused: dict[int, dict] = {}
        for rank, hit in enumerate(bm_hits):
            fused[int(hit.doc_id)] = {
                "hit": hit, "score": 1.0 / (60 + rank + 1), "bm25": True, "vec": False,
            }
        vec_only: list[dict] = []
        seen_vec_docs: set[int] = set()
        for rank, (cid, dist) in enumerate(vec):
            row = by_id.get(int(cid))
            if row is None:
                continue
            doc_id = int(row["doc_id"])
            if doc_id in seen_vec_docs:
                continue
            seen_vec_docs.add(doc_id)
            sim = max(0.0, 1.0 - float(dist) / 2.0)
            if sim < min_sim:
                continue
            if require_lexical and terms:
                blob = (str(row["title"] or "") + " " + str(row["text"] or "")).lower()
                if not any(t.lower() in blob for t in terms):
                    continue
            item = fused.get(doc_id)
            if item is not None:
                item["vec"] = True
                item["score"] += 0.7 / (60 + rank + 1)
                continue
            if len(vec_only) >= vec_max:
                continue
            snippet_html, snippet_text = self._build_snippet(row["text"], terms)
            vec_only.append({
                "hit": SearchHit(
                    doc_id=doc_id,
                    mount_id=int(row["mount_id"]),
                    title=row["title"],
                    rel_path=row["rel_path"],
                    abs_path=row["abs_path"],
                    format=row["format"],
                    score=sim,
                    chunk_seq=int(row["seq"]),
                    snippet_html=snippet_html,
                    snippet_text=snippet_text,
                    route="vector",
                ),
                "score": sim,
            })
        out: list[SearchHit] = []
        for item in sorted(fused.values(), key=lambda x: x["score"], reverse=True):
            hit = item["hit"]
            if item["bm25"] and item["vec"]:
                hit.route = "hybrid"
            hit.score = float(item["score"])
            out.append(hit)
            if len(out) >= topk:
                break
        for item in sorted(vec_only, key=lambda x: x["score"], reverse=True):
            if len(out) >= topk:
                break
            out.append(item["hit"])
        return out
    # ---- 路由 ----
    @staticmethod
    def _content_terms(raw_tokens: list[str]) -> list[str]:
        """提问式查询的术语提取：过滤问词/停用词与弱单字，保留检索有意义的词。

        规则（无需 LLM）：ASCII 字母数字一律保留；中文词长度 ≥2 保留；
        全部被过滤时回退到「非停用词」原始 token（如单字「锁」），全为停用词则返回空。
        """
        kept = [
            t
            for t in raw_tokens
            if t.lower() not in _STOPWORDS
            and (len(t) >= 2 or (t.isascii() and t.isalnum()))
        ]
        if kept:
            return kept
        return [t for t in raw_tokens if t.lower() not in _STOPWORDS]

    @staticmethod
    def _is_question(query: str) -> bool:
        """是否提问式输入（含问句标记词）：决定是否启用去问词过滤与 OR 召回。"""
        return any(marker in query for marker in _QUESTION_MARKERS)

    def _match_phrases(self, phrases: list[str], limit: int):
        """连续短语优先（从长到短），命中即返回；避免分词把复合词拆碎。"""
        for phrase in phrases[:6]:
            if len(phrase) < 2:
                continue
            q = fts_quote(phrase)
            conds = ["(content_jieba : {q}) OR (content : {q})".format(q=q)]
            toks = [sanitize_token(t) for t in query_tokens(phrase)]
            toks = [t for t in toks if t]
            if len(toks) > 1:
                tp = fts_quote(build_phrase_match(toks).strip('"'))
                conds.append("(content_jieba : {q}) OR (content : {q})".format(q=tp))
            rows = self._run(_FTS_TABLE, " OR ".join(conds), limit)
            if rows:
                return rows, "jieba"
        return [], "jieba"

    def _match_or_gated(self, tokens: list[str], limit: int):
        """问句 OR 兜底：只对有效词；单泛词/单字不放行，避免全库泛匹配。"""
        sig = [
            t for t in tokens
            if (len(t) >= 2 or (t.isascii() and t.isalnum())) and t.lower() not in _STOPWORDS
        ]
        if not sig:
            return [], "jieba_or"
        if len(sig) == 1:
            only = sig[0].lower()
            if only in GENERIC_TERMS or (len(sig[0]) < 3 and not sig[0].isascii()):
                return [], "jieba_or"
        parts = []
        for t in sig:
            q = fts_quote(t)
            parts.append("(content_jieba : {q} OR content : {q})".format(q=q))
        rows = self._run(_FTS_TABLE, " OR ".join(parts), max(limit * 3, 48))
        if not rows:
            return [], "jieba_or"
        lowered = [t.lower() for t in sig]
        specific = [t for t in lowered if t not in GENERIC_TERMS]
        scored = []
        for r in rows:
            text = str(r["text"]).lower()
            title = str(r["title"] or "").lower()
            cov = sum(1 for t in lowered if t in text)
            title_cov = sum(1 for t in lowered if t in title)
            has_specific = any(t in text or t in title for t in specific)
            scored.append((cov, title_cov, 1 if has_specific else 0, float(r["score"]), r))
        scored.sort(key=lambda x: (x[0], x[1], x[2], -x[3]), reverse=True)
        kept = [r for cov, _tc, spec, _s, r in scored if cov >= 1 and spec]
        return kept[:limit], "jieba_or"

    def _match_jieba(self, tokens: list[str], limit: int):
        phrase = build_phrase_match(tokens)
        match = "(content_jieba : {p}) OR (content : {p})".format(p=phrase)
        return self._run(_FTS_TABLE, match, limit), "jieba"

    def _match_and(self, tokens: list[str], limit: int):
        jieba_parts = " AND ".join("content_jieba : " + fts_quote(t) for t in tokens)
        content_parts = " AND ".join("content : " + fts_quote(t) for t in tokens)
        match = "({j}) OR ({c})".format(j=jieba_parts, c=content_parts)
        return self._run(_FTS_TABLE, match, limit), "jieba_and"

    def _match_or(self, tokens: list[str], limit: int):
        """OR 召回 + 覆盖度排序（提问式查询兜底）。

        命中 = 任一术语出现；排序先按「命中的不同术语数」降序、再按 bm25；
        过滤：至少命中 min(2, 术语数, 最好覆盖度) 个术语，避免单个泛词把所有文档拉进来。
        """
        parts: list[str] = []
        for t in tokens:
            q = fts_quote(t)
            parts.append("(content_jieba : {q} OR content : {q})".format(q=q))
        match = " OR ".join(parts)
        rows = self._run(_FTS_TABLE, match, max(limit * 3, 48))
        if not rows:
            return [], "jieba_or"
        lowered = [t.lower() for t in tokens]
        scored: list[tuple[int, int, float, object]] = []
        for r in rows:
            text = str(r["text"]).lower()
            title = str(r["title"] or "").lower()
            cov = sum(1 for t in lowered if t in text)
            title_cov = sum(1 for t in lowered if t in title)
            scored.append((cov, title_cov, float(r["score"]), r))
        # 覆盖度优先，其次标题命中（短标题的精确笔记胜过长代码文件），最后 bm25
        scored.sort(key=lambda x: (x[0], x[1], -x[2]), reverse=True)
        best = scored[0][0]
        threshold = min(2, len(tokens), best)
        kept = [r for cov, _tc, _s, r in scored if cov >= threshold]
        return kept[:limit], "jieba_or"

    def _match_trigram(self, query: str, limit: int):
        grams = self._grams(query)
        if not grams:
            return [], "trigram"
        match = " OR ".join(fts_quote(g) for g in grams)
        return self._run(_TRIGRAM_TABLE, match, limit), "trigram"

    def _match_like(self, query: str, limit: int):
        q = query.strip()
        if not q or len(q) > _LIKE_MAX_LEN:
            return [], "like"
        rows = self.db.query(
            "SELECT c.chunk_id, c.doc_id, c.seq, c.text, d.title, d.rel_path, "
            "d.abs_path, d.format, d.mount_id, 0.0 AS score "
            "FROM chunks c JOIN docs d ON d.doc_id = c.doc_id "
            "JOIN mounts m ON m.mount_id = d.mount_id "
            "WHERE d.status='indexed'" + self._policy + " AND c.text LIKE ? LIMIT ?",
            ("%" + q + "%", limit),
        )
        return rows, "like"

    @staticmethod
    def _grams(query: str) -> list[str]:
        """查询整串去空白标点后取 3-gram（去重保序；dsh matchOf 思路）。"""
        cleaned = "".join(ch for ch in query if ch.isalnum() or ord(ch) > 127)
        grams: list[str] = []
        seen: set[str] = set()
        for i in range(len(cleaned) - 2):
            g = cleaned[i : i + 3]
            if g not in seen:
                seen.add(g)
                grams.append(g)
                if len(grams) >= _MAX_GRAMS:
                    break
        return grams

    def _run(self, table: str, match: str, limit: int):
        sql = (
            "SELECT f.rowid AS chunk_id, c.doc_id, c.seq, c.text, "
            "d.title, d.rel_path, d.abs_path, d.format, d.mount_id, "
            "bm25({t}) AS score "
            "FROM {t} AS f "
            "JOIN chunks AS c ON c.chunk_id = f.rowid "
            "JOIN docs AS d ON d.doc_id = c.doc_id "
            "JOIN mounts AS m ON m.mount_id = d.mount_id "
            "WHERE {t} MATCH ? AND d.status='indexed'" + self._policy + " "
            "ORDER BY score LIMIT ?"
        ).format(t=table)
        return self.db.query(sql, (match, limit))

    # ---- snippet ----
    def _build_snippet(self, text: str, tokens: list[str]) -> tuple[str, str]:
        """锚定首个命中位置取 ±window 窗口，转义后 <em> 高亮全部命中（合并重叠）。"""
        w = self.snippet_window
        spans: list[tuple[int, int]] = []
        first = -1
        for tok in tokens:
            start = 0
            while True:
                i = text.find(tok, start)
                if i < 0:
                    break
                spans.append((i, i + len(tok)))
                if first < 0:
                    first = i
                start = i + 1
        if first < 0:
            first = 0
            spans = []
        lo = max(0, first - w)
        hi = min(len(text), first + w)
        # 窗口尽量落在句号/换行边界（前后各回看/前瞻 ≤40 字符）
        m = _TERM_BOUNDARY.search(text[max(0, lo - 40) : lo])
        if m:
            lo = max(0, lo - 40) + m.end()
        m2 = _TERM_BOUNDARY.search(text[hi : min(len(text), hi + 40)])
        if m2:
            hi = hi + m2.end()
        seg = text[lo:hi]
        local = [(max(lo, s) - lo, min(hi, e) - lo) for s, e in spans if e > lo and s < hi]
        merged: list[tuple[int, int]] = []
        for s, e in sorted(local):
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        parts: list[str] = []
        pos = 0
        for s, e in merged:
            parts.append(html.escape(seg[pos:s]))
            parts.append("<em>" + html.escape(seg[s:e]) + "</em>")
            pos = e
        parts.append(html.escape(seg[pos:]))
        body = "".join(parts)
        snippet_html = ("…" if lo > 0 else "") + body + ("…" if hi < len(text) else "")
        return snippet_html, seg

    # ---- 全文查看 ----
    def get_doc_text(self, doc_id: int, max_chars: int = 20000) -> Optional[str]:
        """拼接 chunk 原文（仅 status=indexed；调用方须再复核排除规则）。"""
        doc = self.db.get_doc_by_id(doc_id)
        if doc is None or doc.status != "indexed":
            return None
        text = "\n\n".join(self.db.get_chunk_texts(doc_id))
        return text[:max_chars]