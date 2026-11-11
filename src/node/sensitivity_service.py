"""敏感词检查服务：扫描、待确认列表、确认/忽略与规则变更重扫。"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from indexer.db import IndexDB
from indexer.sensitivity import BUILTIN_RULES, scan_text

log = logging.getLogger("chiselwall.sensitivity")


class SensitivityService:
    def __init__(self, db: IndexDB) -> None:
        self.db = db
        self._scan_lock = threading.Lock()
        self._rescan_thread: Optional[threading.Thread] = None
        self._sync_builtin_rules()

    # ---- 规则 ----
    def _sync_builtin_rules(self) -> None:
        for spec in BUILTIN_RULES:
            self.db.upsert_builtin_sensitivity_rule(
                spec.rule_id, spec.label, spec.category, spec.severity,
                spec.kind, spec.scope, spec.pattern, spec.version, spec.validator,
            )

    def rules(self) -> list[dict]:
        return self.db.list_sensitivity_rules()

    def add_custom_rule(self, label: str, pattern: str, category: str = "", severity: str = "medium") -> dict:
        rule = self.db.add_custom_sensitivity_rule(label, pattern, category, severity)
        self.rescan_async()
        return rule

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> dict:
        rule = self.db.set_sensitivity_rule_enabled(rule_id, enabled)
        self.rescan_async()
        return rule

    def delete_custom_rule(self, rule_id: str) -> None:
        self.db.delete_custom_sensitivity_rule(rule_id)
        self.rescan_async()

    # ---- 扫描 ----
    def scan_doc(self, doc_id: int) -> dict:
        doc = self.db.get_doc_by_id(int(doc_id))
        if doc is None or doc.status != "indexed":
            return {"doc_id": int(doc_id), "hits": 0, "signature": ""}
        sens = self.db.get_doc_sensitivity(doc.doc_id) or {}
        if int(sens.get("ignored") or 0):
            return {"doc_id": doc.doc_id, "hits": 0, "signature": "", "skipped": True}
        mounts = self._mount_map()
        memberships = self._group_memberships()
        if self._is_scope_ignored({"mount_id": doc.mount_id, "rel_path": doc.rel_path}, mounts, memberships):
            return {"doc_id": doc.doc_id, "hits": 0, "signature": "", "skipped": True}
        path_text = (doc.title or "") + "\n" + (doc.rel_path or "")
        text = "\n".join(self.db.get_chunk_texts(doc.doc_id))
        if len(text) > 2_000_000:
            text = text[:2_000_000]
        ignored_rules = self.db.sensitivity_doc_rule_ignores(doc.doc_id)
        rules = [r for r in self.db.list_sensitivity_rules() if r["rule_id"] not in ignored_rules]
        hits, signature = scan_text(text, path_text, rules)
        self.db.set_doc_sensitivity_hits(doc.doc_id, signature, hits)
        return {"doc_id": doc.doc_id, "hits": len(hits), "signature": signature}

    def scan_all(self) -> int:
        if not self._scan_lock.acquire(blocking=False):
            return 0
        try:
            docs = self.db.list_docs(status="indexed", limit=None)
            n = 0
            for doc in docs:
                try:
                    self.scan_doc(int(doc["doc_id"]))
                    n += 1
                except Exception:
                    log.exception("敏感词扫描失败 doc_id=%s", doc.get("doc_id"))
            return n
        finally:
            self._scan_lock.release()

    def rescan_async(self) -> None:
        if self._rescan_thread is not None and self._rescan_thread.is_alive():
            return
        t = threading.Thread(target=self.scan_all, name="sensitivity-rescan", daemon=True)
        self._rescan_thread = t
        t.start()

    def scanning(self) -> bool:
        return bool(self._rescan_thread and self._rescan_thread.is_alive())

    def wait_idle(self, timeout: float = 10.0) -> None:
        t = self._rescan_thread
        if t is not None and t.is_alive():
            t.join(timeout)

    # ---- 生效范围 ----
    def _mount_map(self) -> dict[int, dict]:
        return {int(m.mount_id): {"ignored": bool(m.sensitivity_ignored), "enabled": bool(m.enabled)}
                for m in self.db.list_mounts()}

    def _group_memberships(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT gm.mount_id, gm.rel_path, g.sensitivity_ignored "
            "FROM group_members gm JOIN groups g ON g.group_id=gm.group_id"
        )]

    def _is_scope_ignored(self, doc: dict, mounts: dict[int, dict], memberships: list[dict]) -> bool:
        m = mounts.get(int(doc["mount_id"]))
        if m and m["ignored"]:
            return True
        rel = doc.get("rel_path") or ""
        mine = [x for x in memberships if int(x["mount_id"]) == int(doc["mount_id"])
                and (x["rel_path"] == "" or x["rel_path"] == rel)]
        # 只有挂载/分组忽略；没有分组时不忽略。属于多个组时，任一组正常就仍检查。
        return bool(mine) and all(int(x["sensitivity_ignored"]) for x in mine)

    def _doc_sensitivity_rows(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.db.query("SELECT * FROM doc_sensitivity WHERE doc_id IN (" + marks + ")", ids)
        return {int(r["doc_id"]): dict(r) for r in rows}

    def decorate_docs(self, docs: list[dict]) -> list[dict]:
        ids = [int(d["doc_id"]) for d in docs]
        hits_map = self.db.sensitivity_hits_for_docs(ids)
        sens_map = self._doc_sensitivity_rows(ids)
        mounts = self._mount_map()
        memberships = self._group_memberships()
        for d in docs:
            did = int(d["doc_id"])
            hits = hits_map.get(did, [])
            sens = sens_map.get(did, {})
            pending_hits = [h for h in hits if h.get("severity") in ("high", "medium")]
            d["sensitivity_hit_count"] = len(pending_hits)
            d["sensitivity_high"] = any(h.get("severity") == "high" for h in pending_hits)
            d["sensitivity_hits"] = hits[:5]
            d["sensitivity_signature"] = sens.get("hit_signature", "")
            d["sensitivity_ack"] = sens.get("ack_signature", "")
            d["sensitivity_ignored"] = bool(sens.get("ignored", 0))
            ignored = d["sensitivity_ignored"] or self._is_scope_ignored(d, mounts, memberships)
            mt = mounts.get(int(d["mount_id"])) or {}
            d["sensitivity_pending"] = bool(
                d.get("share_state") == "shared" and mt.get("enabled", True) and not ignored and pending_hits
                and sens.get("hit_signature", "") != sens.get("ack_signature", "")
            )
        return docs

    # ---- 待确认/详情 ----
    def _pending_sql(self, limit: Optional[int]) -> list[dict]:
        sql = (
            "SELECT d.doc_id, d.mount_id, d.rel_path, d.title, d.format, d.share_state, "
            "ds.hit_signature, ds.ack_signature, ds.ignored AS doc_ignored, "
            "(SELECT COUNT(*) FROM sensitivity_hits h WHERE h.doc_id=d.doc_id "
            " AND h.severity IN ('high','medium')) AS hit_count, "
            "(SELECT COUNT(*) FROM sensitivity_hits h WHERE h.doc_id=d.doc_id "
            " AND h.severity='high') AS high_count "
            "FROM docs d JOIN mounts mt ON mt.mount_id=d.mount_id "
            "LEFT JOIN doc_sensitivity ds ON ds.doc_id=d.doc_id "
            "WHERE d.status='indexed' AND d.share_state='shared' AND mt.enabled=1 "
            "AND mt.sensitivity_ignored=0 AND COALESCE(ds.ignored,0)=0 "
            "AND COALESCE(ds.hit_signature,'') != '' "
            "AND COALESCE(ds.hit_signature,'') != COALESCE(ds.ack_signature,'') "
            "AND EXISTS (SELECT 1 FROM sensitivity_hits h WHERE h.doc_id=d.doc_id "
            " AND h.severity IN ('high','medium')) "
            "AND NOT (EXISTS (SELECT 1 FROM group_members gm WHERE gm.mount_id=d.mount_id "
            " AND (gm.rel_path='' OR gm.rel_path=d.rel_path)) "
            " AND NOT EXISTS (SELECT 1 FROM group_members gm JOIN groups g ON g.group_id=gm.group_id "
            " WHERE gm.mount_id=d.mount_id AND (gm.rel_path='' OR gm.rel_path=d.rel_path) "
            " AND g.sensitivity_ignored=0)) "
            "ORDER BY high_count DESC, d.doc_id DESC"
        )
        params: list = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [dict(r) for r in self.db.query(sql, params)]

    def pending_docs(self, limit: Optional[int] = 100) -> list[dict]:
        docs = self._pending_sql(limit)
        hits_map = self.db.sensitivity_hits_for_docs([int(d["doc_id"]) for d in docs])
        for d in docs:
            d["hits"] = hits_map.get(int(d["doc_id"]), [])
        return docs

    def summary(self) -> dict:
        pending = self._pending_sql(None)
        hit_count = sum(int(d.get("hit_count") or 0) for d in pending)
        high_docs = sum(1 for d in pending if int(d.get("high_count") or 0) > 0)
        rules = self.db.list_sensitivity_rules()
        return {
            "pending_docs": len(pending),
            "hit_count": hit_count,
            "high_docs": high_docs,
            "rules": len(rules),
            "enabled_rules": sum(1 for r in rules if int(r.get("enabled", 1))),
            "custom_rules": sum(1 for r in rules if r.get("source") == "custom"),
            "scanning": self.scanning(),
        }

    def pending(self, limit: int = 100) -> list[dict]:
        return self.pending_docs(limit=limit)

    def doc_detail(self, doc_id: int) -> dict:
        doc = self.db.get_doc_by_id(int(doc_id))
        if doc is None:
            raise LookupError("文档不存在")
        sens = self.db.get_doc_sensitivity(doc.doc_id) or {}
        hits = self.db.sensitivity_hits_for_doc(doc.doc_id)
        mounts = self._mount_map()
        memberships = self._group_memberships()
        scope_ignored = self._is_scope_ignored({"mount_id": doc.mount_id, "rel_path": doc.rel_path}, mounts, memberships)
        ignored = bool(sens.get("ignored", 0)) or scope_ignored
        pending_hits = [h for h in hits if h.get("severity") in ("high", "medium")]
        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "rel_path": doc.rel_path,
            "share_state": doc.share_state,
            "ignored": ignored,
            "doc_ignored": bool(sens.get("ignored", 0)),
            "scope_ignored": scope_ignored,
            "hit_signature": sens.get("hit_signature", ""),
            "ack_signature": sens.get("ack_signature", ""),
            "hits": hits,
            "pending": bool(doc.share_state == "shared" and not ignored and pending_hits
                            and sens.get("hit_signature", "") != sens.get("ack_signature", "")),
        }

    def ack_doc(self, doc_id: int) -> dict:
        doc = self.db.get_doc_by_id(int(doc_id))
        if doc is None:
            raise LookupError("文档不存在")
        sens = self.db.get_doc_sensitivity(doc.doc_id) or {}
        self.db.ack_doc_sensitivity(doc.doc_id, sens.get("hit_signature", ""))
        return self.doc_detail(doc.doc_id)

    def set_doc_ignored(self, doc_id: int, ignored: bool) -> dict:
        doc = self.db.get_doc_by_id(int(doc_id))
        if doc is None:
            raise LookupError("文档不存在")
        self.db.set_doc_sensitivity_ignored(doc.doc_id, ignored)
        if not ignored:
            self.scan_doc(doc.doc_id)
        return self.doc_detail(doc.doc_id)

    def ignore_rule_for_doc(self, doc_id: int, rule_id: str) -> dict:
        doc = self.db.get_doc_by_id(int(doc_id))
        if doc is None:
            raise LookupError("文档不存在")
        if self.db.get_sensitivity_rule(rule_id) is None:
            raise LookupError("规则不存在")
        self.db.add_doc_sensitivity_rule_ignore(doc.doc_id, rule_id)
        self.scan_doc(doc.doc_id)
        return self.doc_detail(doc.doc_id)

    # ---- 挂载/分组忽略 ----
    def set_mount_ignored(self, mount_id: int, ignored: bool) -> dict:
        if self.db.get_mount(int(mount_id)) is None:
            raise LookupError("挂载不存在")
        self.db.set_mount_sensitivity_ignored(int(mount_id), ignored)
        if not ignored:
            self.rescan_async()  # 忽略期间可能新增/修改过文档，恢复时补扫
        return self.summary()

    def set_group_ignored(self, group_id: int, ignored: bool) -> dict:
        if self.db.get_group(int(group_id)) is None:
            raise LookupError("分组不存在")
        self.db.set_group_sensitivity_ignored(int(group_id), ignored)
        if not ignored:
            self.rescan_async()
        return self.summary()
