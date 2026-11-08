"""笔记分组单测（设计 §15.6 / GR-07 裁决；M3-2）：

建组/删组、归组（整挂载 + 具体文档、一来源可属多组）、按组筛选与统计、
整组批量排除 = 生成真实 exclude_rules 记录（API/DB 可查）、删组不影响规则、
规则可单独删除（删除后重扫恢复可见）、deny-union、幂等与错误路径。
运行：python -m unittest tests.test_groups
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from node.agent import Config, NodeAgent

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notes"


class GroupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "notes"
        shutil.copytree(FIXTURES, self.root)
        self.agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
        mount = self.agent.add_mount(str(self.root))
        self.mid = mount["mount_id"]
        self.agent.indexer.scan_mount(self.mid, self.agent.connector_of(self.mid))

    def tearDown(self) -> None:
        self.agent.stop()
        self.tmp.cleanup()

    def test_group_crud_members_and_filter(self) -> None:
        g = self.agent.add_group("数据库组")
        gid = g["group_id"]
        self.assertEqual([x["name"] for x in self.agent.db.list_groups()], ["数据库组"])
        # 整挂载归组 + 具体文档归组（一来源可属多组）
        self.agent.add_group_member(gid, self.mid, "")
        g2 = self.agent.add_group("备份组")["group_id"]
        self.agent.add_group_member(g2, self.mid, "中文笔记.md")
        self.assertEqual(len(self.agent.db.list_group_members(gid)), 1)
        self.assertEqual(len(self.agent.db.list_group_members(g2)), 1)
        # 统计：整挂载组 = 全部已索引文档；单文档组 = 1
        self.assertEqual(self.agent.db.group_doc_count(gid), self.agent.db.stats()["indexed"])
        self.assertEqual(self.agent.db.group_doc_count(g2), 1)
        # 按组筛选文档列表
        docs = self.agent.db.list_docs(group_id=g2)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["rel_path"], "中文笔记.md")
        # 删组：成员级联删除
        self.agent.remove_group(g2)
        self.assertEqual([x["name"] for x in self.agent.db.list_groups()], ["数据库组"])

    def test_group_batch_exclude_real_rules(self) -> None:
        # GR-07 裁决核心：整组批量排除 = 生成真实排除规则记录
        gid = self.agent.add_group("整组排除测试")["group_id"]
        self.agent.add_group_member(gid, self.mid, "")  # 整挂载 -> dir 规则
        res = self.agent.exclude_group(gid)
        self.assertEqual(len(res["rules_created"]), 1)
        self.assertEqual(res["rules_created"][0]["kind"], "dir")
        self.assertEqual(res["rules_created"][0]["pattern"], str(self.root))
        # 规则真实可查（API/DB 可见）
        rules = self.agent.db.list_exclude_rules()
        self.assertEqual(len(rules), 1)
        # deny-union 立即生效：文档翻转为 excluded、chunk 摘除、全文不可得
        doc = self.agent.db.get_doc(self.mid, "中文笔记.md")
        self.assertEqual(doc.status, "excluded")
        self.assertEqual(len(self.agent.db.get_chunk_texts(doc.doc_id)), 0)
        # 删组不影响规则
        self.agent.remove_group(gid)
        self.assertEqual(len(self.agent.db.list_exclude_rules()), 1)
        # 规则可单独删除 -> 重扫恢复可见
        self.agent.remove_exclude_rule(rules[0][0])
        self.agent.indexer.scan_mount(self.mid, self.agent.connector_of(self.mid))
        doc2 = self.agent.db.get_doc(self.mid, "中文笔记.md")
        self.assertEqual(doc2.status, "indexed")
        self.assertGreater(len(self.agent.db.get_chunk_texts(doc2.doc_id)), 0)

    def test_group_exclude_single_doc_file_rule_idempotent(self) -> None:
        gid = self.agent.add_group("单文档组")["group_id"]
        self.agent.add_group_member(gid, self.mid, "单独排除.md")
        res = self.agent.exclude_group(gid)
        self.assertEqual(len(res["rules_created"]), 1)
        self.assertEqual(res["rules_created"][0]["kind"], "file")
        self.assertEqual(res["rules_created"][0]["pattern"], str(self.root / "单独排除.md"))
        # 幂等：重复整组排除不重复建规则
        res2 = self.agent.exclude_group(gid)
        self.assertEqual(len(res2["rules_created"]), 0)
        self.assertEqual(len(self.agent.db.list_exclude_rules()), 1)

    def test_error_paths(self) -> None:
        with self.assertRaises(LookupError):
            self.agent.exclude_group(9999)
        with self.assertRaises(LookupError):
            self.agent.remove_group(9999)
        with self.assertRaises(LookupError):
            self.agent.add_group_member(9999, self.mid)
        with self.assertRaises(ValueError):
            self.agent.add_group("   ")


if __name__ == "__main__":
    unittest.main()
