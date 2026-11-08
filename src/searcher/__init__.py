"""检索包（设计 §5/§7/§12）：关键词 topk、bm25、路由兜底与上下文片段。

路由：jieba 短语（P0 主索引）-> AND 降级 -> trigram 兜底（P1）-> 短查询 LIKE。
预留（§16，只登记不动工）：向量混合检索（vec0）、gram idf 截断、跨节点 BM25 归一化。
"""
from .search import SearchHit, Searcher

__all__ = ["SearchHit", "Searcher"]
