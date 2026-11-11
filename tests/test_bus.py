"""M4 广播问答单测：relay 路由/限流/在线状态 + 聚合去重排序 + 提问视图。

运行：python -m unittest tests.test_bus
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from node.bus import AskService, NodeBus, RelayConfig, aggregate
from relay.server import RelayHub


class RelayGroupRegistryTest(unittest.TestCase):
    """M6：自由建组 + 描述 + 全员可见（组注册表持久化）。"""

    def test_create_and_describe(self) -> None:
        import tempfile
        from pathlib import Path

        from relay.server import GroupStore

        with tempfile.TemporaryDirectory() as tmp:
            store = GroupStore(Path(tmp) / "groups.json")
            hub = RelayHub({"alice": "t"}, group_store=store)
            result = hub.create_group("数据库组", "数据库相关笔记与问题", "alice")
            self.assertTrue(result["ok"])
            dup = hub.create_group("数据库组", "x", "bob")
            self.assertFalse(dup["ok"])
            view = {g["name"]: g for g in hub.groups_view()}
            self.assertEqual(view["数据库组"]["description"], "数据库相关笔记与问题")
            # 重建 hub：注册表持久化生效
            hub2 = RelayHub({"alice": "t"}, group_store=store)
            view2 = {g["name"]: g for g in hub2.groups_view()}
            self.assertIn("数据库组", view2)

    def test_auto_create_on_join_and_online_counts(self) -> None:
        hub = RelayHub({"alice": "t", "bob": "t2"})

        async def _send(_p: dict) -> None:
            return None

        hub.register("alice", ["数据库组"], _send)
        hub.register("bob", ["数据库组", "前端组"], _send)
        view = {g["name"]: g for g in hub.groups_view()}
        self.assertIn("数据库组", view)
        self.assertEqual(view["数据库组"]["online"], 2)
        self.assertEqual(view["前端组"]["online"], 1)
        self.assertIn("全员组", view)


class RelayHubTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = RelayHub({"alice": "tok_a", "bob": "tok_b", "carol": "tok_c"}, rate=0.2, burst=2)

        async def _send(_p: dict) -> None:
            return None

        self.send = _send

    def _register(self, user: str, groups: list[str]):
        return self.hub.register(user, groups, self.send)

    def test_auth(self) -> None:
        self.assertTrue(self.hub.auth("alice", "tok_a"))
        self.assertFalse(self.hub.auth("alice", "bad"))
        self.assertFalse(self.hub.auth("nobody", "tok_a"))

    def test_register_and_online(self) -> None:
        self._register("alice", ["全员组"])
        self._register("bob", ["数据库组"])
        online = {c["user"]: c["groups"] for c in self.hub.online()}
        self.assertEqual(set(online), {"alice", "bob"})
        self.assertIn("全员组", online["bob"])  # 组列表恒含全员组

    def test_route_query_group_semantics(self) -> None:
        self._register("alice", ["全员组"])
        self._register("bob", ["数据库组"])
        self._register("carol", ["前端组"])
        # 全员组 -> 除提问者外所有在线节点
        targets = {c.user for c in self.hub.route_query("alice", ["全员组"])}
        self.assertEqual(targets, {"bob", "carol"})
        # 指定组 -> 只发给组内成员
        targets = {c.user for c in self.hub.route_query("alice", ["数据库组"])}
        self.assertEqual(targets, {"bob"})
        # 空组 -> 视为全员组
        targets = {c.user for c in self.hub.route_query("alice", [])}
        self.assertEqual(targets, {"bob", "carol"})

    def test_rate_limit_query_publisher(self) -> None:
        self.assertTrue(self.hub.allow_query("alice"))
        self.assertTrue(self.hub.allow_query("alice"))
        self.assertFalse(self.hub.allow_query("alice"))  # burst=2 用完

    def test_research_session_has_own_burst(self) -> None:
        for _ in range(4):
            self.assertTrue(self.hub.allow_query("alice", "research-1"))
        self.assertFalse(self.hub.allow_query("alice", "research-1"))
        # research 桶不消耗普通提问桶
        self.assertTrue(self.hub.allow_query("alice"))

    def test_unregister_respects_replacement(self) -> None:
        self._register("alice", ["全员组"])

        async def other(_p: dict) -> None:
            return None

        self.hub.register("alice", ["全员组"], other)
        # 旧连接注销不应删掉新连接
        self.hub.unregister("alice", self.send)
        self.assertIn("alice", self.hub.clients)
        self.hub.unregister("alice", other)
        self.assertNotIn("alice", self.hub.clients)

    def test_known_groups_union(self) -> None:
        self._register("alice", ["全员组"])
        self._register("bob", ["数据库组"])
        self._register("carol", ["前端组"])
        names = [g["name"] for g in self.hub.groups_view()]
        self.assertEqual(names, sorted(["全员组", "数据库组", "前端组"]))

    def test_heartbeat_sweep(self) -> None:
        self._register("alice", ["全员组"])
        self.hub.clients["alice"].last_seen -= 100
        self.assertEqual(self.hub.sweep(), ["alice"])
        self.assertEqual(self.hub.online(), [])


class AggregateTest(unittest.TestCase):
    def _hit(self, title: str, rel: str, snippet: str, score: float, route: str = "jieba") -> dict:
        return {
            "title": title, "rel_path": rel, "format": "md", "doc_id": 1,
            "chunk_seq": 0, "score": score, "snippet_html": snippet, "snippet_text": snippet, "route": route,
        }

    def test_exact_dedup_and_agreement(self) -> None:
        hit = self._hit("文件锁", "fcntl.md", "fcntl 是系统调用", 0.5)
        agg = aggregate([{"node": "bob", "hits": [hit]}, {"node": "carol", "hits": [hit]}])
        self.assertEqual(len(agg), 1)
        self.assertEqual(set(agg[0]["nodes"]), {"bob", "carol"})
        self.assertEqual(agg[0]["agreement"], 2)

    def test_near_duplicate_merge(self) -> None:
        a = self._hit("笔记A", "a.md", "同一段内容 来自不同人的拷贝", 0.4)
        b = self._hit("笔记B", "b.md", "同一段内容 来自不同人的拷贝", 0.6)
        agg = aggregate([{"node": "bob", "hits": [a]}, {"node": "carol", "hits": [b]}])
        self.assertEqual(len(agg), 1)
        self.assertEqual(agg[0]["agreement"], 2)

    def test_rank_order_and_topk(self) -> None:
        hits_a = [self._hit("一", "1.md", "甲", 0.1), self._hit("二", "2.md", "乙", 0.2)]
        hits_b = [self._hit("三", "3.md", "丙", 0.1)]
        agg = aggregate([{"node": "bob", "hits": hits_a}, {"node": "carol", "hits": hits_b}], topk=2)
        self.assertEqual(len(agg), 2)
        self.assertEqual(agg[0]["title"], "一")  # bob 第一名 rank 分最高

    def test_only_some_terms_no_crash(self) -> None:
        self.assertEqual(aggregate([]), [])
        self.assertEqual(aggregate([{"node": "bob", "hits": []}]), [])


class AskServiceTest(unittest.TestCase):
    def test_view_collecting_then_done(self) -> None:
        svc = AskService(deadline_ms=200)
        item = svc.create("fcntl 怎么加锁", ["全员组"])
        svc.note_ack(item.qid, 2)
        view = svc.view(item.qid, online=[{"user": "bob"}])
        self.assertEqual(view["status"], "collecting")
        self.assertEqual(view["expected"], 2)
        self.assertEqual(view["responded"], 0)
        svc.note_answer(item.qid, {"node": "bob", "qid": item.qid, "elapsed_ms": 5,
                                   "hits": [{"title": "文件锁", "rel_path": "a.md", "snippet_text": "fcntl", "snippet_html": "fcntl", "score": 0.1, "route": "jieba", "format": "md", "doc_id": 1, "chunk_seq": 0}]})
        svc.note_answer(item.qid, {"node": "carol", "qid": item.qid, "elapsed_ms": 9, "hits": []})
        view = svc.view(item.qid, online=[])
        self.assertEqual(view["status"], "done")
        self.assertEqual(view["responded"], 2)
        self.assertEqual(len(view["aggregated"]), 1)

    def test_view_unknown(self) -> None:
        self.assertIsNone(AskService().view("nope", online=[]))


class NodeBusAskTest(unittest.TestCase):
    def test_ask_broadcasts_refined_query(self) -> None:
        class FakeHit:
            title = "文件锁"
            tier = "strong"

            def to_dict(self) -> dict:
                return {"title": self.title, "tier": self.tier, "hits": []}

        class FakeSearcher:
            def search(self, text: str, _topk: int = 8):
                if text == "文件锁 用法":
                    return [FakeHit()]
                return []

        cfg = RelayConfig(enabled=True, relay_url="ws://x", nick="alice")
        bus = NodeBus(
            cfg, FakeSearcher(),
            rewrite_fn=lambda q: ["文件锁 用法"],
        )
        bus.client.connected = True
        sent: list = []

        async def fake_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        bus.client.send = fake_send  # type: ignore[assignment]
        original = "请说明下文件锁怎么用"
        r = asyncio.run(bus.ask(original))
        self.assertEqual(sent[0]["type"], "query")
        self.assertEqual(sent[0]["text"], "文件锁 用法")
        self.assertEqual(sent[0]["question"], original)
        view = bus.view(r["qid"])
        self.assertEqual(view["text"], original)
        self.assertEqual(view["search_text"], "文件锁 用法")

    def test_ask_prefers_original_when_both_hit(self) -> None:
        class FakeHit:
            title = "文件锁"
            tier = "strong"

            def to_dict(self) -> dict:
                return {"title": self.title, "tier": self.tier}

        class FakeSearcher:
            def search(self, text: str, _topk: int = 8):
                return [FakeHit()]

        cfg = RelayConfig(enabled=True, relay_url="ws://x", nick="alice")
        bus = NodeBus(cfg, FakeSearcher(), rewrite_fn=lambda q: ["文件锁 用法"])
        bus.client.connected = True
        sent: list = []

        async def fake_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        bus.client.send = fake_send  # type: ignore[assignment]
        original = "请说明下文件锁怎么用"
        asyncio.run(bus.ask(original))
        self.assertEqual(sent[0]["text"], original)

    def test_ask_includes_local_answer(self) -> None:
        class FakeHit:
            def to_dict(self) -> dict:
                return {"title": "本机笔记", "rel_path": "local.md", "snippet_text": "fcntl", "snippet_html": "fcntl", "score": 0.1, "route": "jieba", "format": "md", "doc_id": 1, "chunk_seq": 0}

        class FakeSearcher:
            def search(self, _text: str, _topk: int = 8):
                return [FakeHit()]

        cfg = RelayConfig(enabled=True, relay_url="ws://x", nick="alice")
        bus = NodeBus(cfg, FakeSearcher())
        sent: list = []

        async def fake_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        bus.client.send = fake_send  # type: ignore[assignment]
        bus.client.connected = True  # 模拟已连接
        r = asyncio.run(bus.ask("fcntl"))
        qid = r["qid"]
        self.assertEqual(sent[0]["type"], "query")
        view = bus.view(qid)
        self.assertEqual(view["responded"], 1)  # 本机答案已记录
        self.assertEqual(view["expected"], 1)   # 期望 = 本机 1（远端 ack 未到）
        self.assertEqual(view["aggregated"][0]["nodes"], ["alice"])
        bus.asks.note_ack(qid, 1)
        bus.asks.note_answer(qid, {"node": "bob", "qid": qid, "elapsed_ms": 3, "hits": []})
        view = bus.view(qid)
        self.assertEqual(view["expected"], 2)
        self.assertEqual(view["responded"], 2)


class NodeBusFetchTest(unittest.TestCase):
    """M4.1 远端全文预览：仅允许取本次搜索返回过的文档。"""

    def _bus(self):
        class FakeHit:
            def __init__(self, doc_id: int) -> None:
                self.doc_id = doc_id

            def to_dict(self) -> dict:
                return {"doc_id": self.doc_id, "title": "t", "rel_path": "a.md",
                        "snippet_text": "x", "snippet_html": "x", "score": 0.1,
                        "route": "jieba", "format": "md", "chunk_seq": 0}

        class FakeSearcher:
            def search(self, text: str, topk: int = 8):
                return [FakeHit(7)]

            def get_doc_text(self, doc_id: int, max_chars: int = 20000) -> str:
                return "全文内容"

        bus = NodeBus(RelayConfig(enabled=True, relay_url="ws://x", nick="alice"), FakeSearcher())
        bus.client.connected = True
        sent: list = []

        async def fake_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        bus.client.send = fake_send  # type: ignore[assignment]
        return bus, sent

    def test_fetch_allowed_after_answer(self) -> None:
        bus, sent = self._bus()
        asyncio.run(bus._handle_query({"type": "query", "asker": "bob", "qid": "q1", "text": "x"}))
        asyncio.run(bus._handle_fetch({"type": "fetch", "asker": "bob", "req_id": "r1", "doc_id": 7}))
        results = [m for m in sent if m["type"] == "fetch_result"]
        self.assertEqual(results[0]["text"], "全文内容")
        self.assertEqual(results[0]["error"], "")

    def test_fetch_denied_when_not_returned(self) -> None:
        bus, sent = self._bus()
        asyncio.run(bus._handle_fetch({"type": "fetch", "asker": "bob", "req_id": "r2", "doc_id": 7}))
        results = [m for m in sent if m["type"] == "fetch_result"]
        self.assertIn("不在最近搜索结果", results[0]["error"])

    def test_fetch_roundtrip(self) -> None:
        bus, sent = self._bus()

        async def run() -> str:
            task = asyncio.create_task(bus.fetch("bob", 7, timeout_s=3))
            await asyncio.sleep(0.05)
            req = [m for m in sent if m["type"] == "fetch"][0]
            self.assertEqual(req["to"], "bob")
            await bus._on_message({
                "type": "fetch_result", "to": "alice", "req_id": req["req_id"],
                "doc_id": 7, "text": "远端全文", "error": "",
            })
            return await task

        self.assertEqual(asyncio.run(run()), "远端全文")


class NodeBusAskValidationTest(unittest.TestCase):
    def _bus(self) -> NodeBus:
        class FakeSearcher:
            def search(self, _text: str, _topk: int = 8):
                return []

        bus = NodeBus(RelayConfig(enabled=True, relay_url="ws://x", nick="alice"), FakeSearcher())
        bus.client.connected = True

        async def fake_send(_payload: dict) -> bool:
            return True

        bus.client.send = fake_send  # type: ignore[assignment]
        return bus

    def test_empty_group_selection_rejected(self) -> None:
        bus = self._bus()
        with self.assertRaises(ValueError):
            asyncio.run(bus.ask("fcntl", []))

    def test_none_groups_use_own_groups(self) -> None:
        bus = self._bus()
        r = asyncio.run(bus.ask("fcntl", None))
        self.assertEqual(r["groups"], ["全员组"])


class NodeBusInboundTest(unittest.TestCase):
    def test_handle_query_answers_and_rate_limits(self) -> None:
        class FakeSearcher:
            def search(self, _text: str, _topk: int = 8):
                return []

        cfg = RelayConfig(enabled=True, relay_url="ws://x", nick="alice")
        bus = NodeBus(cfg, FakeSearcher())
        sent: list = []

        async def fake_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        bus.client.send = fake_send  # type: ignore[assignment]

        async def run() -> None:
            await bus._handle_query({"type": "query", "asker": "bob", "qid": "q1", "text": "fcntl"})
            await bus._handle_query({"type": "query", "asker": "bob", "qid": "q2", "text": "fcntl"})
            await bus._handle_query({"type": "query", "asker": "bob", "qid": "q3", "text": "fcntl"})

        asyncio.run(run())
        self.assertEqual(len(sent), 2)  # burst=2，第三次被限流丢弃
        self.assertTrue(all(p["type"] == "answer" for p in sent))
        self.assertEqual(sent[0]["qid"], "q1")
        self.assertEqual(sent[0]["node"], "alice")

    def test_ignore_own_query(self) -> None:
        class FakeSearcher:
            def search(self, _text: str, _topk: int = 8):
                return []

        bus = NodeBus(RelayConfig(enabled=True, relay_url="ws://x", nick="alice"), FakeSearcher())
        sent: list = []

        async def fake_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        bus.client.send = fake_send  # type: ignore[assignment]
        asyncio.run(bus._handle_query({"type": "query", "asker": "alice", "qid": "q1", "text": "x"}))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()