"""L0/L1 敏感词扫描、确认与忽略范围。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indexer.db import IndexDB
from indexer.jieba_util import tokenize
from node.sensitivity_service import SensitivityService


class SensitivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = IndexDB(Path(self.tmp.name) / "s.db")
        self.mount = self.db.add_mount("directory", str(Path(self.tmp.name) / "notes"))
        self.service = SensitivityService(self.db)
        self.doc = self._doc("客户合同.md", "客户合同", "合同金额 100 万，联系电话 13800138000")
        self.service.scan_doc(self.doc)

    def tearDown(self):
        self.service.wait_idle()
        self.db.close()
        self.tmp.cleanup()

    def _doc(self, rel: str, title: str, text: str) -> int:
        doc_id = self.db.upsert_doc(
            self.mount, rel, str(Path(self.tmp.name) / "notes" / rel), title, "md", 1, 10, rel, "indexed"
        )
        self.db.set_chunks(doc_id, [text], tokenize)
        return doc_id

    def _hit_rules(self, doc_id: int) -> set[str]:
        return {h["rule_id"] for h in self.service.doc_detail(doc_id)["hits"]}

    def test_scan_and_pending(self):
        self.assertIn("builtin.biz.contract_amount", self._hit_rules(self.doc))
        self.assertIn("builtin.pii.phone", self._hit_rules(self.doc))
        self.assertEqual(self.service.summary()["pending_docs"], 1)

    def test_private_and_hidden_do_not_pending(self):
        self.db.set_doc_share(self.doc, "private")
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        self.db.set_doc_share(self.doc, "hidden")
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        self.db.set_doc_share(self.doc, "shared")
        self.assertEqual(self.service.summary()["pending_docs"], 1)

    def test_ack_is_per_document(self):
        other = self._doc("另一份合同.md", "另一份合同", "合同金额 200 万")
        self.service.scan_doc(other)
        self.assertEqual(self.service.summary()["pending_docs"], 2)
        self.service.ack_doc(self.doc)
        self.assertFalse(self.service.doc_detail(self.doc)["pending"])
        self.assertTrue(self.service.doc_detail(other)["pending"])
        self.assertEqual(self.service.summary()["pending_docs"], 1)

    def test_new_rule_reappears_after_ack(self):
        self.service.ack_doc(self.doc)
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        self.service.add_custom_rule("合同", "合同")
        self.service.scan_doc(self.doc)  # add_custom 是异步；测试里直接重扫
        self.assertTrue(self.service.doc_detail(self.doc)["pending"])

    def test_block_builtin_rule(self):
        rule = next(r for r in self.service.rules() if r["rule_id"] == "builtin.biz.contract_amount")
        self.service.set_rule_enabled(rule["rule_id"], False)
        self.service.scan_doc(self.doc)
        self.assertNotIn("builtin.biz.contract_amount", self._hit_rules(self.doc))
        self.service.set_rule_enabled(rule["rule_id"], True)
        self.service.scan_doc(self.doc)
        self.assertIn("builtin.biz.contract_amount", self._hit_rules(self.doc))

    def test_builtin_cannot_delete_custom_can(self):
        with self.assertRaises(ValueError):
            self.service.delete_custom_rule("builtin.biz.contract_amount")
        rule = self.service.add_custom_rule("项目名", "密项目")
        self.service.delete_custom_rule(rule["rule_id"])
        self.assertIsNone(self.db.get_sensitivity_rule(rule["rule_id"]))

    def test_mount_and_group_ignore(self):
        self.service.set_mount_ignored(self.mount, True)
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        self.service.set_mount_ignored(self.mount, False)
        group = self.db.add_group("合同组")
        self.db.add_group_member(group, self.mount, "")
        self.service.set_group_ignored(group, True)
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        # 同时属于一个正常组时仍应检查
        active = self.db.add_group("正常组")
        self.db.add_group_member(active, self.mount, "")
        self.assertEqual(self.service.summary()["pending_docs"], 1)

    def test_ignored_mount_skips_scan_and_restores(self):
        self.service.set_mount_ignored(self.mount, True)
        new_doc = self._doc("新合同.md", "新合同", "合同金额 300 万")
        result = self.service.scan_doc(new_doc)
        self.assertTrue(result.get("skipped"))
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        self.service.set_mount_ignored(self.mount, False)
        self.service.wait_idle()
        self.assertGreaterEqual(self.service.summary()["pending_docs"], 1)

    def test_doc_ignore_and_per_rule_ignore(self):
        self.service.set_doc_ignored(self.doc, True)
        self.assertEqual(self.service.summary()["pending_docs"], 0)
        self.service.set_doc_ignored(self.doc, False)
        self.service.ignore_rule_for_doc(self.doc, "builtin.biz.contract_amount")
        self.assertNotIn("builtin.biz.contract_amount", self._hit_rules(self.doc))
        self.assertTrue(self.service.doc_detail(self.doc)["pending"])

    def test_validator_and_word_boundary(self):
        bad = self._doc("bad.md", "bad", "身份证 110105194912310021")  # 校验位错误
        tokenizer = self._doc("tokenizer.md", "tokenizer", "tokenizer only")
        self.service.scan_doc(bad)
        self.service.scan_doc(tokenizer)
        self.assertNotIn("builtin.pii.cn_id", self._hit_rules(bad))
        self.assertNotIn("builtin.path.secret", self._hit_rules(tokenizer))

    def test_excerpt_masks_credentials(self):
        token = "github_pat_" + "A1b2C3d4" * 5
        secret = self._doc("secret.md", "secret", "token: " + token + " and sk-" + "Z9y8X7w6" * 3)
        self.service.scan_doc(secret)
        detail = self.service.doc_detail(secret)
        text = " ".join(h["excerpt"] for h in detail["hits"])
        self.assertNotIn(token, text)
        self.assertNotIn("sk-Z9y8X7w6", text)

    def test_low_confidence_does_not_light_dot(self):
        low = self._doc("mail.md", "mail", "联系 someone@example.com")
        self.service.scan_doc(low)
        self.assertIn("builtin.pii.email", self._hit_rules(low))
        self.assertFalse(self.service.doc_detail(low)["pending"])


if __name__ == "__main__":
    unittest.main()
