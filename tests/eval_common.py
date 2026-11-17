"""评测脚本共用：golden schema 解析与期望文档集合。"""
from __future__ import annotations

from typing import Any


def expected_doc_ids(db: Any, item: dict) -> set[int]:
    """支持 expect（精确标题）、expect_title_like（SQL LIKE）、expect_doc_ids。"""
    expected: set[int] = {int(x) for x in (item.get("expect_doc_ids") or [])}
    for title in item.get("expect") or []:
        rows = db.query(
            "SELECT doc_id FROM docs WHERE status='indexed' AND title=?",
            (str(title),),
        )
        expected.update(int(r["doc_id"]) for r in rows)
    for pattern in item.get("expect_title_like") or []:
        rows = db.query(
            "SELECT doc_id FROM docs WHERE status='indexed' AND title LIKE ?",
            (str(pattern),),
        )
        expected.update(int(r["doc_id"]) for r in rows)
    return expected
