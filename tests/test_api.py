"""API 单测（设计 §11）：token 鉴权（P0）、挂载/排除 CRUD、搜索、全文红线、
失败列表、静态页。TestClient 进程内直测，不真正打开文件管理器。

运行：python -m unittest tests.test_api
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from api.server import APP_VERSION, create_app
from node.agent import Config, NodeAgent

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notes"
TOKEN = "test-token-123"
H = {"X-Chisel-Token": TOKEN}


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "notes"
        shutil.copytree(FIXTURES, self.root)
        self.agent = NodeAgent(Config(db_path=base / "c.db", config_dir=base / "cfg"))
        self.client = TestClient(create_app(self.agent, token=TOKEN))
        # 同步执行初始扫描（不走后台 worker，测试确定性）
        mount = self.agent.add_mount(str(self.root))
        self.mount_id = mount["mount_id"]
        self.agent.indexer.scan_mount(self.mount_id, self.agent.connector_of(self.mount_id))

    def tearDown(self) -> None:
        self.agent.stop()
        self.tmp.cleanup()

    def _search(self, q: str) -> list:
        r = self.client.get("/api/search", params={"q": q}, headers=H)
        self.assertEqual(r.status_code, 200)
        return r.json()["hits"]

    # ---- 鉴权（评审结论③ P0） ----
    def test_health_public(self) -> None:
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertNotIn("db", r.json())  # F4：不暴露本机绝对路径
        self.assertEqual(r.json()["version"], APP_VERSION)  # FIX5：版本单一来源

    def test_token_required(self) -> None:
        self.assertEqual(self.client.get("/api/stats").status_code, 401)
        self.assertEqual(self.client.get("/api/mounts").status_code, 401)
        self.assertEqual(self.client.get("/api/search", params={"q": "x"}).status_code, 401)

    def test_token_ok(self) -> None:
        r = self.client.get("/api/stats", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["indexed"], 13)
        # M2-FIX4：排除构成分类字段
        cats = r.json()["excluded_by_reason"]
        self.assertEqual(set(cats.keys()), {"user_rule", "default", "unsupported_format", "duplicate_md_twin"})
        self.assertGreaterEqual(cats["unsupported_format"], 1)

    # ---- 搜索 ----
    def test_search_hit(self) -> None:
        hits = self._search("凿壁测试词条")
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["rel_path"], "中文笔记.md")
        self.assertIn("<em>", hits[0]["snippet_html"])
        self.assertEqual(hits[0]["route"], "jieba")

    def test_search_topk_capped(self) -> None:
        hits = self._search("测试")
        self.assertLessEqual(len(hits), 8)  # 默认 topk=8

    # ---- 挂载管理 ----
    def test_mounts_list(self) -> None:
        r = self.client.get("/api/mounts", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["path"], str(self.root))

    def test_add_mount_invalid(self) -> None:
        r = self.client.post(
            "/api/mounts", json={"path": str(Path(self.tmp.name) / "不存在")}, headers=H
        )
        self.assertEqual(r.status_code, 400)

    def test_add_mount_db_dir_rejected(self) -> None:
        # 红线②：索引库所在目录禁止挂载
        r = self.client.post("/api/mounts", json={"path": str(Path(self.tmp.name))}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn("红线", r.json()["detail"])

    def test_add_siyuan_mount(self) -> None:
        # M3-1：siyuan 挂载类型端到端（挂载 -> 扫描 -> 检索命中）
        ws = Path(__file__).resolve().parent / "fixtures" / "siyuan-workspace"
        r = self.client.post("/api/mounts", json={"path": str(ws), "kind": "siyuan"}, headers=H)
        self.assertEqual(r.status_code, 200)
        mid = r.json()["mount_id"]
        self.agent.indexer.scan_mount(mid, self.agent.connector_of(mid))
        hits = self._search("思源测试词条")
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["format"], "sy")

    def test_add_and_remove_mount(self) -> None:
        sub = Path(self.tmp.name) / "extra"
        sub.mkdir()
        (sub / "x.md").write_text("# X\n额外挂载词条。\n", encoding="utf-8")
        r = self.client.post("/api/mounts", json={"path": str(sub)}, headers=H)
        self.assertEqual(r.status_code, 200)
        mid = r.json()["mount_id"]
        self.agent.indexer.scan_mount(mid, self.agent.connector_of(mid))
        self.assertGreaterEqual(len(self._search("额外挂载词条")), 1)
        r = self.client.delete("/api/mounts/" + str(mid), headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/mounts", headers=H).json().__len__(), 1)

    # ---- 排除规则与红线⑤ ----
    def test_exclude_rule_crud_and_redline(self) -> None:
        secret = self.agent.db.get_doc(self.mount_id, "排除我/secret.md")
        self.assertIsNotNone(secret)
        doc_id = secret.doc_id
        hits = self._search("机密词条")
        self.assertTrue(any(h["doc_id"] == doc_id for h in hits))
        pattern = str(self.root / "排除我")
        r = self.client.post("/api/excludes", json={"kind": "dir", "pattern": pattern}, headers=H)
        self.assertEqual(r.status_code, 200)
        # 规则立即生效：被排除文档搜索不可得、全文 403（其他含该词的文档仍可命中）
        self.assertTrue(all(h["doc_id"] != doc_id for h in self._search("机密词条")))
        r = self.client.get("/api/docs/" + str(doc_id), headers=H)
        self.assertEqual(r.status_code, 403)
        # 打开所在位置同样被拒（红线⑤，未真正调用文件管理器）
        r = self.client.get("/api/docs/" + str(doc_id) + "/open-location", headers=H)
        self.assertEqual(r.status_code, 403)
        # 删除规则 + 重扫 -> 恢复
        rules = self.client.get("/api/excludes", headers=H).json()
        self.client.delete("/api/excludes/" + str(rules[0]["rule_id"]), headers=H)
        self.agent.indexer.scan_mount(self.mount_id, self.agent.connector_of(self.mount_id))
        self.assertGreaterEqual(len(self._search("机密词条")), 1)

    def test_exclude_rule_invalid_kind(self) -> None:
        r = self.client.post("/api/excludes", json={"kind": "bad", "pattern": "x"}, headers=H)
        self.assertEqual(r.status_code, 400)

    # ---- 文档查看 ----
    def test_open_alternatives_shape(self) -> None:
        # M2-FIX6：无关联时 /open 返回应用内 alternatives（mock agent 方法，不真实启动程序）
        with mock.patch.object(
            self.agent,
            "open_file",
            return_value=(True, "无默认关联", "请选择打开方式",
                          [{"app": "notepad", "label": "用记事本打开", "path": "notepad.exe"}]),
        ):
            r = self.client.get("/api/docs/1/open", headers=H)
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["how"], "无默认关联")
            self.assertEqual(body["alternatives"][0]["app"], "notepad")

    def test_open_with(self) -> None:
        with mock.patch.object(self.agent, "open_with", return_value=(True, "用记事本打开", "")) as m:
            r = self.client.get("/api/docs/1/open-with", params={"app": "notepad"}, headers=H)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])
            m.assert_called_once_with(1, "notepad")

    def test_open_with_unknown_app(self) -> None:
        with mock.patch.object(
            self.agent, "open_with", return_value=(False, "", "该应用不可用（未安装或未找到）: xyz")
        ):
            r = self.client.get("/api/docs/1/open-with", params={"app": "xyz"}, headers=H)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["ok"])

    def test_doc_text_ok_and_missing(self) -> None:
        doc = self.agent.db.get_doc(self.mount_id, "中文笔记.md")
        r = self.client.get("/api/docs/" + str(doc.doc_id), headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertIn("凿壁测试词条", r.json()["text"])
        r = self.client.get("/api/docs/999999", headers=H)
        self.assertEqual(r.status_code, 404)

    # ---- 失败列表 / 重索引 / 静态页 ----
    def test_failed_list(self) -> None:
        r = self.client.get("/api/failed", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("corrupt.docx" in f["rel_path"] for f in r.json()))

    def test_reindex(self) -> None:
        r = self.client.post("/api/reindex", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_groups_endpoints_gr07(self) -> None:
        # M3-2/GR-07：建组 -> 归组 -> 整组排除（真实规则）-> 删组不影响规则 -> 规则可单独删
        r = self.client.post("/api/groups", json={"name": "验收组"}, headers=H)
        self.assertEqual(r.status_code, 200)
        gid = r.json()["group_id"]
        r = self.client.post(
            "/api/groups/" + str(gid) + "/members",
            json={"mount_id": self.mount_id, "rel_path": ""},
            headers=H,
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/groups", headers=H)
        self.assertEqual(len(r.json()), 1)
        self.assertGreater(r.json()[0]["docs"], 0)
        # 整组排除：生成真实规则
        r = self.client.post("/api/groups/" + str(gid) + "/exclude", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["rules_created"]), 1)
        rules = self.client.get("/api/excludes", headers=H).json()
        self.assertGreaterEqual(len(rules), 1)
        # 删组：规则仍在（GR-07）
        self.client.delete("/api/groups/" + str(gid), headers=H)
        self.assertGreaterEqual(len(self.client.get("/api/excludes", headers=H).json()), 1)
        # 规则单独删除 + 重扫 -> 恢复可见
        for rule in rules:
            self.client.delete("/api/excludes/" + str(rule["rule_id"]), headers=H)
        self.agent.indexer.scan_mount(self.mount_id, self.agent.connector_of(self.mount_id))
        self.assertGreaterEqual(len(self._search("凿壁测试词条")), 1)

    def test_docs_listing_and_excluded(self) -> None:
        # M3-2：文档列表（挂载/分组过滤）+「排除/不支持格式」明细与失败列表分开（观察-1）
        r = self.client.get("/api/docs", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()), 10)
        r2 = self.client.get("/api/docs", params={"mount_id": self.mount_id}, headers=H)
        self.assertEqual(len(r2.json()), len(r.json()))
        r3 = self.client.get("/api/excluded", headers=H)
        self.assertGreaterEqual(len(r3.json()), 5)
        self.assertFalse(any("corrupt.docx" in d["rel_path"] for d in r3.json()))  # 失败在另一个列表

    def test_sensitivity_endpoints(self) -> None:
        r = self.client.get("/api/sensitivity/summary", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["rules"], 0)
        r = self.client.get("/api/sensitivity/rules", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()["rules"]), 0)
        r = self.client.get("/api/docs", headers=H)
        self.assertIn("sensitivity_pending", r.json()[0])
        custom = self.client.post("/api/sensitivity/rules", json={"label": "测试敏感词", "pattern": "凿壁测试词条"}, headers=H)
        self.assertEqual(custom.status_code, 200)
        rid = custom.json()["rule_id"]
        self.assertEqual(self.client.delete("/api/sensitivity/rules/" + rid, headers=H).status_code, 200)
        self.assertEqual(self.client.delete("/api/sensitivity/rules/builtin.biz.compensation", headers=H).status_code, 400)
        self.assertEqual(self.client.patch("/api/mounts/" + str(self.mount_id), json={"sensitivity_ignored": True}, headers=H).status_code, 200)
        self.assertEqual(self.client.patch("/api/mounts/" + str(self.mount_id), json={"sensitivity_ignored": False}, headers=H).status_code, 200)
        g = self.client.post("/api/groups", json={"name": "敏感测试组"}, headers=H).json()
        self.assertEqual(self.client.patch("/api/groups/" + str(g["group_id"]), json={"sensitivity_ignored": True}, headers=H).status_code, 200)
        self.client.delete("/api/groups/" + str(g["group_id"]), headers=H)

    def test_sensitivity_new_doc_auto_scan(self) -> None:
        path = self.root / "new-sensitive.md"
        path.write_text("# 新笔记\n\n合同金额 123 万，联系电话 13800138000\n", encoding="utf-8")
        self.agent.indexer.index_file(self.mount_id, self.agent.connector_of(self.mount_id), "new-sensitive.md")
        docs = self.client.get("/api/docs", headers=H).json()
        doc = next(d for d in docs if d["rel_path"] == "new-sensitive.md")
        self.assertTrue(doc["sensitivity_pending"])
        self.assertGreaterEqual(doc["sensitivity_hit_count"], 1)
        detail = self.client.get("/api/sensitivity/docs/" + str(doc["doc_id"]), headers=H)
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()["pending"])
        ack = self.client.post("/api/sensitivity/docs/" + str(doc["doc_id"]) + "/ack", headers=H)
        self.assertEqual(ack.status_code, 200)
        self.assertFalse(ack.json()["pending"])

    def test_static_ui_served(self) -> None:
        # M2-FIX5：首页注入版本（?v= 缓存破坏 + 页脚版本号）+ no-cache 响应头
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("凿壁", r.text)
        self.assertIn("随手记", r.text)
        self.assertIn("app.js?v=" + APP_VERSION, r.text)
        self.assertIn("style.css?v=" + APP_VERSION, r.text)
        self.assertIn("v" + APP_VERSION, r.text)  # 页脚版本展示
        self.assertNotIn("__CHISEL_VERSION__", r.text)  # 占位符已替换
        self.assertEqual(r.headers.get("cache-control"), "no-cache")

    def test_static_assets_no_cache(self) -> None:
        # M2-FIX5：静态资源一律 no-cache（根因：浏览器旧缓存致 UI 修复不可见）
        for path in ("/app.js?v=" + APP_VERSION, "/style.css?v=" + APP_VERSION):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers.get("cache-control"), "no-cache", path)


if __name__ == "__main__":
    unittest.main()
