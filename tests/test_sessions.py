"""M8：AI 会话存储 / 多轮历史 / 追问上下文。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.rag import build_messages
from ai.sessions import AISessionStore
from node.agent import Config, NodeAgent
from node.bus import AskItem
import time


class SessionStoreTest(unittest.TestCase):
    def test_crud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AISessionStore(Path(tmp) / "s.db")
            s = store.create("测试")
            self.assertEqual(store.list_sessions()[0]["title"], "测试")
            store.append(s["id"], "user", "问一")
            store.append(s["id"], "assistant", "答一")
            self.assertEqual(len(store.messages(s["id"])), 2)
            self.assertTrue(store.rename(s["id"], "改名"))
            self.assertEqual(store.get(s["id"])["title"], "改名")
            self.assertTrue(store.delete(s["id"]))
            self.assertEqual(store.list_sessions(), [])
            store.close()


class AskSessionStoreTest(unittest.TestCase):
    def test_turns_and_payload(self) -> None:
        from ai.ask_sessions import AskSessionStore

        with tempfile.TemporaryDirectory() as tmp:
            store = AskSessionStore(Path(tmp) / "a.db")
            s = store.create("会话")
            t = store.append(s["id"], "user", "问题", {"qid": "q1", "aggregated": [{"title": "t"}]})
            self.assertTrue(store.update_payload(t["id"], {"qid": "q1", "aggregated": [{"title": "t2"}]}))
            turns = store.turns(s["id"])
            self.assertEqual(turns[0]["payload"]["aggregated"][0]["title"], "t2")
            store.append(s["id"], "assistant", "回答")
            self.assertEqual(len(store.turns(s["id"])), 2)
            self.assertTrue(store.delete(s["id"]))
            store.close()


class HistoryTest(unittest.TestCase):
    def test_build_messages_with_history(self) -> None:
        msgs = build_messages(
            "那 Java 呢", "资料", "sys",
            history=[{"question": "文件锁怎么用", "answer": "用 flock"}],
        )
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1], {"role": "user", "content": "文件锁怎么用"})
        self.assertEqual(msgs[2], {"role": "assistant", "content": "用 flock"})
        self.assertIn("那 Java 呢", msgs[-1]["content"])

    def test_history_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = NodeAgent(Config(db_path=Path(tmp) / "c.db", config_dir=Path(tmp) / "cfg"))
            try:
                pairs = agent._history_pairs([
                    {"role": "user", "content": "q1"},
                    {"role": "assistant", "content": "a1"},
                    {"role": "user", "content": "q2"},
                ])
                self.assertEqual(pairs, [{"question": "q1", "answer": "a1"}])
            finally:
                agent.stop()


class FollowupTest(unittest.TestCase):
    def test_followup_uses_previous_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = NodeAgent(Config(db_path=Path(tmp) / "c.db", config_dir=Path(tmp) / "cfg"))
            try:
                item = AskItem(qid="q1", text="文件锁怎么用", groups=["全员组"],
                               created_at=time.monotonic(), deadline_at=time.monotonic() + 10)
                agent.bus.asks.items["q1"] = item
                agent.ai_answer = lambda q, agg, history=None: {"status": "ready", "answer": "答：" + q, "citations": []}
                result = agent.ask_followup("q1", "那 Java 呢")
                self.assertTrue(result["ok"])
                self.assertEqual(result["followups"][0]["text"], "那 Java 呢")
                self.assertEqual(result["followups"][0]["answer"]["answer"], "答：那 Java 呢")
            finally:
                agent.stop()


if __name__ == "__main__":
    unittest.main()
