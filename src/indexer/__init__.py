"""索引包（设计 §5）：切分、SQLite FTS5 索引、增量监视与资源控制。

M2-2：chunker（切分）/ jieba_util（分词）/ db（索引库）/ indexer（索引管线）
M2-3：resources（低优先级+空闲调度）/ watcher（watchdog 增量）
预留（§16，只登记不动工）：
    - trigram 兜底表已随 db.py 建表（P1），查询路由在 searcher（M2-4）
    - sqlite-vec 向量表、部门自定义词典重索引——见 db.py / jieba_util.py 注释
"""
from .chunker import ChunkResult, Chunker
from .db import DocRecord, IndexDB, MountRecord
from .indexer import Indexer, file_hash
from .jieba_util import build_phrase_match, fts_quote, query_tokens, sanitize_token, tokenize

__all__ = [
    "ChunkResult",
    "Chunker",
    "DocRecord",
    "IndexDB",
    "MountRecord",
    "Indexer",
    "file_hash",
    "build_phrase_match",
    "fts_quote",
    "query_tokens",
    "sanitize_token",
    "tokenize",
]
