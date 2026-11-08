"""jieba 中文分词工具（调研 02 §1.1、调研 03 §2.3：影子列方案）。

入库与查询必须使用同一分词函数（lcut_for_search，搜索引擎模式细粒度更高），
否则短语查询无法命中（调研 03 §2.3②）。

查询净化（调研 03 §2.1/§2.3⑥）：每个 token 用双引号包裹成 FTS5 字符串字面量
（内部引号翻倍），并做白名单字符过滤——引号包裹后 FTS5 特殊字符全部按字面处理，
从根本上杜绝语法注入。

预留（§16，只登记不动工）：
    - 部门自定义词典：jieba.load_userdict（~/.chiselwall/userdict.txt），
      词典更新须触发全量重索引（调研 03 §2.3⑤）
"""
from __future__ import annotations

import logging

_jieba = None

# 查询 token 允许保留的 ASCII 标点（引号包裹后均为字面量）
_SAFE_ASCII = "_.-/"


def _get_jieba():
    """惰性加载 jieba：首次构建词典缓存约数百 ms；常驻进程只加载一次。"""
    global _jieba
    if _jieba is None:
        import jieba

        jieba.setLogLevel(logging.WARNING)  # 静默词典构建日志
        _jieba = jieba
    return _jieba


def tokenize(text: str) -> str:
    """全文切词，空格连接——写入 chunks.text_jieba 影子列。"""
    if not text:
        return ""
    jb = _get_jieba()
    tokens = [t.strip() for t in jb.lcut_for_search(text)]
    return " ".join(t for t in tokens if t)


def query_tokens(query: str) -> list[str]:
    """查询切词（去空、按序去重）。与入库同一分词函数。"""
    if not query:
        return []
    jb = _get_jieba()
    seen: set[str] = set()
    out: list[str] = []
    for t in jb.lcut_for_search(query):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def sanitize_token(token: str) -> str:
    """白名单净化：保留字母数字、CJK/非 ASCII、少量安全标点；其余剔除。"""
    out: list[str] = []
    for ch in token:
        if ch.isalnum() or ord(ch) > 127 or ch in _SAFE_ASCII:
            out.append(ch)
    return "".join(out).strip(" .-/")


def fts_quote(token: str) -> str:
    """把 token 变成 FTS5 字符串字面量（双引号包裹、内部引号翻倍）。"""
    return '"' + token.replace('"', '""') + '"'



# 高频泛词：单独命中不足以召回（避免「文件」这类词把全库拉进来）
GENERIC_TERMS = frozenset(
    "文件 系统 方法 问题 使用 说明 相关 内容 数据 配置 管理 服务 功能 信息 记录 测试 代码 时间 用户 平台 安装 版本 文档 笔记".split()
)

# 问句词（用于从原始查询里剥离，得到更精确的短语候选）
_QUESTION_WORDS = (
    "请说明下", "请说明", "说明下", "请帮我看看", "请帮我", "帮我看看", "帮我",
    "请介绍一下", "介绍一下", "介绍下", "解释一下", "解释下", "告诉我",
    "想问下", "想请教下", "请问下", "请问",
    "是什么", "什么", "怎么", "怎样", "如何", "为什么", "为何", "可以", "是否",
    "哪些", "哪个", "哪里", "有没有", "是不是", "吗", "呢", "一下", "看下",
)


def query_phrases(query: str, *, limit: int = 8) -> list[str]:
    """从原始查询生成短语候选（不依赖分词）：

    - 先剥离问句词，保留连续片段；
    - 对连续 CJK/字母数字片段生成整段 + 3-gram + 2-gram 候选；
    - 再拼上 jieba 的长词（>=2）；去重、按长度降序、截断。
    """
    import re

    text = (query or "").strip()
    for w in sorted(_QUESTION_WORDS, key=len, reverse=True):
        text = text.replace(w, " ")
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    segments = [s.strip() for s in text.split() if len(s.strip()) >= 2]
    if len(segments) > 1:
        return []  # 多段查询交给 AND，避免只命中其中一段
    phrases: list[str] = []
    for seg in segments:
        phrases.append(seg)
        for i in range(0, max(0, len(seg) - 3 + 1)):
            gram = seg[i:i + 3]
            if len(gram) == 3 and gram not in GENERIC_TERMS:
                phrases.append(gram)
    for tok in query_tokens(query):
        tok = sanitize_token(tok)
        if len(tok) >= 2 and tok not in GENERIC_TERMS:
            phrases.append(tok)
    seen: set[str] = set()
    out: list[str] = []
    for p in sorted(phrases, key=len, reverse=True):
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= limit:
            break
    return out

def build_phrase_match(tokens: list[str]) -> str:
    """把查询 token 拼成 FTS5 短语查询（reviewer AC-01-13，实测结论）：

    多个独立引号串 "a" "b" "c" 在 FTS5 中是 AND 语义（词可散落误命中）；
    相邻短语必须写成单个字符串 "a b c"。searcher 与测试统一走本函数，
    防止两处实现漂移。
    """
    cleaned = [t.replace('"', '""') for t in tokens if t]
    return '"' + " ".join(cleaned) + '"'
