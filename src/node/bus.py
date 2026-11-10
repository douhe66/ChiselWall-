"""节点侧 relay 客户端 + 广播问答（M4，设计 §4/§8/§12/§15.5）。

流程：随手问 -> 本地生成 qid -> WebSocket 发 query（按组）-> relay 转发到在线节点
      -> 各节点本地检索（带时间预算/限流/并发上限）-> answer 回传 -> 提问端聚合展示。

聚合（§15.5 L2/L3 规则版）：按（标题, 相对路径）与片段前缀去重；节点内按命中顺序计
rank 分；多节点命中同一内容加权（共识信号）；按（共识数, 分数）排序取 top。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("chiselwall.bus")

try:
    import websockets
except ImportError:  # Linux 缺依赖时不要静默死任务；统一在 info()/UI 暴露
    websockets = None  # type: ignore[assignment]

ALL_GROUP = "全员组"


@dataclass
class RelayConfig:
    enabled: bool = False
    relay_url: str = ""          # ws://127.0.0.1:18760/ws
    nick: str = "我"
    groups: list[str] = field(default_factory=lambda: [ALL_GROUP])
    relay_token: str = ""
    ask_deadline_ms: int = 5000
    answer_budget_ms: int = 800


@dataclass
class AskItem:
    qid: str
    text: str
    groups: list[str]
    created_at: float
    deadline_at: float
    search_text: str = ""
    plan: Optional[dict] = None
    expected: int = 0
    answers: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    ai: Optional[dict] = None
    ai_started: bool = False
    display_enriched: bool = False
    reranked: bool = False
    followups: list[dict] = field(default_factory=list)


class TokenBucket:
    def __init__(self, rate: float = 0.2, burst: int = 2) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class AskService:
    """提问记录（qid）与增量结果视图（进程内，M4 不持久化）。"""

    def __init__(self, deadline_ms: int = 5000) -> None:
        self.deadline_ms = deadline_ms
        self.items: dict[str, AskItem] = {}

    def create(self, text: str, groups: list[str], search_text: str = "", plan: Optional[dict] = None) -> AskItem:
        now = time.monotonic()
        item = AskItem(
            qid=uuid.uuid4().hex[:16],
            text=text,
            groups=groups,
            search_text=search_text or text,
            plan=plan,
            created_at=now,
            deadline_at=now + self.deadline_ms / 1000.0,
        )
        self.items[item.qid] = item
        self._sweep(now)
        return item

    def _sweep(self, now: float, ttl: float = 600.0) -> None:
        for qid in [q for q, it in self.items.items() if now - it.created_at > ttl]:
            self.items.pop(qid, None)

    def note_ack(self, qid: str, targets: int) -> None:
        it = self.items.get(qid)
        if it is not None:
            it.expected = int(targets)

    def note_error(self, qid: str, code: str) -> None:
        it = self.items.get(qid)
        if it is not None:
            it.error = code

    def note_answer(self, qid: str, msg: dict) -> None:
        it = self.items.get(qid)
        if it is not None:
            it.answers.append(msg)

    def view(self, qid: str, *, online: list[dict]) -> Optional[dict]:
        it = self.items.get(qid)
        if it is None:
            return None
        now = time.monotonic()
        local = [a for a in it.answers if a.get("local")]
        remote = [a for a in it.answers if not a.get("local")]
        if it.error:
            status = "error"
        elif now >= it.deadline_at or (it.expected > 0 and len(remote) >= it.expected):
            status = "done"
        else:
            status = "collecting"
        agg = aggregate([{"node": a.get("node", "?"), "hits": a.get("hits") or []} for a in it.answers])
        return {
            "qid": qid,
            "text": it.text,
            "search_text": it.search_text,
            "plan": it.plan,
            "groups": it.groups,
            "status": status,
            "error": it.error,
            "elapsed_ms": int((now - it.created_at) * 1000),
            "online": online,
            "expected": it.expected + (1 if local else 0),
            "responded": len(it.answers),
            "answers": [
                {
                    "node": a.get("node", "?"),
                    "elapsed_ms": a.get("elapsed_ms", 0),
                    "hit_count": len(a.get("hits") or []),
                }
                for a in it.answers
            ],
            "aggregated": agg,
            "ai": it.ai,
            "followups": it.followups,
        }


def _hit_key(hit: dict) -> tuple:
    """精确去重键：标题 + 相对路径。"""
    return (str(hit.get("title") or ""), str(hit.get("rel_path") or ""))


def _snippet_key(hit: dict) -> str:
    """近重复键：片段前 60 字（去空白），识别不同人持有的同一内容。"""
    text = str(hit.get("snippet_text") or "")
    return "".join(text.split())[:60]


def aggregate(answers: list[dict], *, topk: int = 30) -> list[dict]:
    """多节点结果聚合（设计 §15.5 L2/L3 规则版）。

    answers: [{"node": "bob", "hits": [hit, ...]}]（各节点 hits 已按本地相关性排序）
    返回：按（共识数, 排名分）排序的条目，条目含 nodes（哪些节点命中）。
    """
    merged: dict[tuple, dict] = {}
    for ans in answers:
        node = str(ans.get("node") or "?")
        for idx, hit in enumerate(ans.get("hits") or []):
            if not isinstance(hit, dict):
                continue
            key = _hit_key(hit)
            dup = _snippet_key(hit)
            entry = merged.get(key)
            if entry is None and dup:
                for cand in merged.values():
                    if cand.get("_dup") and cand["_dup"] == dup:
                        entry = cand
                        break
            rank_score = 1.0 / (idx + 1.0)
            source = {
                "node": node,
                "doc_id": hit.get("doc_id"),
                "rel_path": hit.get("rel_path"),
                "title": hit.get("title"),
            }
            if entry is None:
                entry = dict(hit)
                entry["_dup"] = dup
                entry["nodes"] = [node]
                entry["sources"] = [source]
                entry["_score"] = rank_score
                entry["agreement"] = 1
                merged[key] = entry
            else:
                if node not in entry["nodes"]:
                    entry["nodes"].append(node)
                    entry["sources"].append(source)
                    entry["agreement"] = len(entry["nodes"])
                entry["_score"] = max(entry["_score"], rank_score)
    out = list(merged.values())
    for e in out:
        e.pop("_dup", None)
        e["score"] = round(float(e.pop("_score", 0.0)) + 0.15 * (e.get("agreement", 1) - 1), 4)
    out.sort(key=lambda e: (e.get("agreement", 1), e.get("score", 0.0)), reverse=True)
    return out[:topk]


class RelayClient:
    """WebSocket 长连接（重连退避 + 心跳 + 消息分发）。"""

    def __init__(self, cfg: RelayConfig, on_message: Callable[[dict], Awaitable[None]]) -> None:
        self.cfg = cfg
        self.on_message = on_message
        self.connected = False
        self.online: list[dict] = []
        self.known_groups: list[dict] = []
        self.last_error = ""
        self._group_waiters: dict[str, asyncio.Future] = {}
        self._ws: Any = None
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    @property
    def dependency_ok(self) -> bool:
        return websockets is not None

    def start(self) -> None:
        if not self.cfg.enabled or not self.cfg.relay_url:
            return
        if websockets is None:
            self.last_error = "缺少 websockets 依赖：请执行 pip install -r requirements.txt 后重启"
            log.error(self.last_error)
            return
        self._task = asyncio.create_task(self._run(), name="chiselwall-relay")

    async def create_group(self, name: str, description: str = "") -> dict:
        """发送建组请求并等待 relay 确认（10s 超时）。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("组名不能为空")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._group_waiters[name] = fut
        try:
            ok = await self.send(
                {"type": "group_create", "name": name, "description": description or ""}
            )
            if not ok:
                raise ValueError("relay 发送失败")
            result = await asyncio.wait_for(fut, 10.0)
        except asyncio.TimeoutError:
            raise ValueError("relay 未确认（可能已断开）") from None
        finally:
            self._group_waiters.pop(name, None)
        return result

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def send(self, payload: dict) -> bool:
        ws = self._ws
        if ws is None or not self.connected:
            return False
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            log.exception("relay send failed")
            return False

    async def _heartbeat(self, ws: Any) -> None:
        try:
            while True:
                await asyncio.sleep(10.0)
                await ws.send(json.dumps({"type": "presence"}))
        except (asyncio.CancelledError, Exception):
            return

    async def _run(self) -> None:
        if websockets is None:
            self.last_error = "缺少 websockets 依赖：请执行 pip install -r requirements.txt 后重启"
            log.error(self.last_error)
            return
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.cfg.relay_url, max_size=4 * 1024 * 1024) as ws:
                    self._ws = ws
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "user": self.cfg.nick,
                                "token": self.cfg.relay_token,
                                "groups": self.cfg.groups,
                            },
                            ensure_ascii=False,
                        )
                    )
                    self.connected = True
                    self.last_error = ""
                    backoff = 1.0
                    hb = asyncio.create_task(self._heartbeat(ws))
                    try:
                        while not self._stop.is_set():
                            raw = await ws.recv()
                            try:
                                msg = json.loads(raw)
                            except ValueError:
                                continue
                            if msg.get("type") in ("online", "hello_ok"):
                                self.online = list(msg.get("online") or [])
                                self.known_groups = list(msg.get("groups") or [])
                            elif msg.get("type") == "group_ok":
                                fut = self._group_waiters.pop(str(msg.get("name") or ""), None)
                                if fut is not None and not fut.done():
                                    fut.set_result({"ok": True, "name": msg.get("name")})
                            await self.on_message(msg)
                    finally:
                        hb.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc) or exc.__class__.__name__
                log.warning("relay 连接失败：%s（%.0fs 后重连）", exc, backoff)
            finally:
                self.connected = False
                self._ws = None
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2.0, 30.0)


async def probe_relay(url: str, nick: str, token: str, groups: list[str], timeout_s: float = 5.0) -> dict:
    """不落盘、不改配置的一次性 relay 握手测试（设置页“测试连接”）。"""
    if websockets is None:
        raise ValueError("缺少 websockets 依赖：请执行 pip install -r requirements.txt 后重启")
    url = (url or "").strip()
    if not url.startswith(("ws://", "wss://")):
        raise ValueError("Relay 地址需以 ws:// 或 wss:// 开头")
    nick = (nick or "").strip()
    if not nick:
        raise ValueError("请先填写昵称")
    started = time.monotonic()
    try:
        async with websockets.connect(url, max_size=4 * 1024 * 1024, open_timeout=timeout_s) as ws:
            await ws.send(json.dumps({
                "type": "hello", "user": nick, "token": token, "groups": groups or [ALL_GROUP],
            }, ensure_ascii=False))
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise ValueError("Relay 握手超时（%.0fs）" % timeout_s) from None
    except Exception as exc:
        raise ValueError("无法连接 Relay：%s" % exc) from exc
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("Relay 返回了无法解析的握手响应") from None
    if msg.get("type") == "error":
        raise ValueError(str(msg.get("error") or "Relay 拒绝连接"))
    if msg.get("type") != "hello_ok":
        raise ValueError("Relay 握手异常：%s" % msg.get("type"))
    return {
        "ok": True,
        "nick": msg.get("user") or nick,
        "online": len(msg.get("online") or []),
        "groups": len(msg.get("groups") or []),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


class NodeBus:
    """节点广播总线：提问发起 + 对端应答 + 结果视图。"""

    def __init__(self, cfg: RelayConfig, searcher: Any, search_fn: Any = None,
                 rewrite_fn: Any = None, plan_fn: Any = None, local_searcher: Any = None) -> None:
        self.cfg = cfg
        self.searcher = searcher
        self.search_fn = search_fn
        self.rewrite_fn = rewrite_fn
        self.plan_fn = plan_fn
        self.local_searcher = local_searcher or searcher
        self.asks = AskService(deadline_ms=cfg.ask_deadline_ms)
        self.client = RelayClient(cfg, self._on_message)
        self._inbound_buckets: dict[str, TokenBucket] = {}
        self._semaphore = asyncio.Semaphore(2)
        self._answer_deadline_s = max(cfg.answer_budget_ms, 100) / 1000.0
        # 远端预览（M4.1）：只允许取「本次搜索返回给该提问者」的文档全文
        self._allowed: dict[str, dict[int, float]] = {}
        self._fetch_buckets: dict[str, TokenBucket] = {}
        self._pending: dict[str, asyncio.Future] = {}

    # ---- 生命周期 ----
    def start(self) -> None:
        self.client.start()

    async def stop(self) -> None:
        await self.client.stop()

    @property
    def connected(self) -> bool:
        return self.client.connected

    @property
    def online(self) -> list[dict]:
        return self.client.online

    def info(self) -> dict:
        return {
            "enabled": self.cfg.enabled,
            "relay_url": self.cfg.relay_url,
            "nick": self.cfg.nick,
            "groups": self.cfg.groups,
            "connected": self.connected,
            "online": self.online,
            "known_groups": self.client.known_groups,
            "server_groups": self.client.known_groups,
            "last_error": self.client.last_error,
            "dependency_ok": self.client.dependency_ok,
        }

    async def create_group(self, name: str, description: str = "") -> dict:
        """自由建组（relay 持久化 + 广播给所有人）。"""
        if not self.connected:
            raise ValueError("relay 未连接")
        return await self.client.create_group(name, description)

    # ---- 提问 ----
    async def ask(self, text: str, groups: Optional[list[str]] = None) -> dict:
        if not self.cfg.enabled:
            raise ValueError("未配置 relay（relay_url/nick/token），无法使用随手问")
        if not self.connected:
            raise ValueError("relay 未连接（检查地址/网络后重试）")
        if groups is not None and not [g for g in groups if str(g).strip()]:
            raise ValueError("至少选择一个提问组（不勾选=不广播；本机笔记仍会参与）")
        gs = [g for g in (groups or self.cfg.groups) if g] or [ALL_GROUP]
        original = text
        refined = text
        plan = None
        plan_dict = None
        if self.plan_fn is not None:
            # 发送前生成结构化计划；未启用/未配置/失败都回退原问题，不调用旧改写。
            try:
                plan = await asyncio.to_thread(self.plan_fn, text)
            except Exception:
                log.exception("检索计划生成失败，回退原问题")
                plan = None
            plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else None
        elif self.rewrite_fn is not None:
            # 兼容旧调用/测试：仅在未提供 plan_fn 时保留旧的单查询改写。
            try:
                probe = await asyncio.to_thread(self.searcher.search, text, 5)
            except Exception:
                probe = []
            variants: list = []
            if not any(getattr(h, "tier", "strong") == "strong" for h in probe):
                try:
                    variants = await asyncio.to_thread(self.rewrite_fn, text)
                except Exception:
                    variants = []
            if variants:
                refined = self._pick_broadcast_query(text, [str(v).strip() for v in variants if str(v).strip()])
        send_text = original if self.plan_fn is not None else refined
        item = self.asks.create(original, gs, search_text=send_text, plan=plan_dict)
        # 本机检索与远端使用同一份计划；没有计划则原问题普通检索。
        local_started = time.monotonic()
        try:
            if plan is not None:
                from ai.query_plan import evaluate_plan

                local_hits = await asyncio.to_thread(evaluate_plan, self.local_searcher, plan, 8)
            else:
                # 计划未生成时直接用本地原问题检索，避免在回退路径上二次调用 LLM
                local_hits = await asyncio.to_thread(self.local_searcher.search, refined, 8)
        except Exception:
            log.exception("本机检索失败")
            local_hits = []
        self.asks.note_answer(
            item.qid,
            {
                "node": self.cfg.nick,
                "elapsed_ms": round((time.monotonic() - local_started) * 1000),
                "hits": [h.to_dict() for h in local_hits],
                "local": True,
            },
        )
        ok = await self.client.send(
            {
                "type": "query",
                "qid": item.qid,
                "text": send_text,
                "question": original,
                "plan": plan_dict,
                "groups": gs,
                "deadline_ms": self.cfg.ask_deadline_ms,
            }
        )
        if not ok:
            raise ValueError("relay 发送失败（连接已断开）")
        return {"qid": item.qid, "groups": gs, "deadline_ms": self.cfg.ask_deadline_ms, "plan": plan_dict}

    def view(self, qid: str) -> Optional[dict]:
        return self.asks.view(qid, online=self.online)

    def _pick_broadcast_query(self, original: str, variants: list[str]) -> str:
        """只有候选明显更好时才替换原查询；平手/略好都保留原查询，避免改写退化。"""
        from indexer.jieba_util import GENERIC_TERMS, query_phrases, query_tokens

        edge = set("的了是我你他她它们这那什么怎样如何吗呢吧啊呀请帮看下")

        def meaningful(text: str) -> bool:
            if len(text) < 3 or text in GENERIC_TERMS:
                return False
            if text[0] in edge or text[-1] in edge:
                return False
            return not any(w in text for w in ("怎么", "什么", "干嘛", "如何", "为什么"))

        candidates: list[str] = [p for p in query_phrases(original) if meaningful(p)]
        for v in variants:
            v = (v or "").strip()
            if len(v) >= 2:
                candidates.append(v)
        candidates.append(original)
        seen: set[str] = set()
        uniq = [c for c in candidates if c and not (c in seen or seen.add(c))]
        terms = [t for t in query_tokens(original) if len(t) >= 2]

        def score(cand: str) -> float:
            try:
                hits = self.searcher.search(cand, 5)
            except Exception:
                return -1.0
            strong = [h for h in hits if getattr(h, "tier", "strong") != "weak"]
            s = 0.0
            if strong:
                s += 2.0
                top = str(strong[0].title or "")
                if terms and any(t in top for t in terms):
                    s += 2.0
                if len(strong) >= 2:
                    s += 0.5
            return s

        orig_score = score(original)
        # 原查询已有强命中就绝不改写：避免把正确结果换成更泛的改写词
        if orig_score >= 2.0:
            return original
        best, best_score = original, orig_score
        for cand in uniq:
            if cand == original:
                continue
            s = score(cand)
            if s > best_score + 0.75:
                best, best_score = cand, s
        return best

    # ---- 检索与查看 ----
    # ---- 应答 ----
    async def _on_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "query":
            await self._handle_query(msg)
        elif typ == "query_ack":
            self.asks.note_ack(str(msg.get("qid") or ""), int(msg.get("targets") or 0))
        elif typ == "answer":
            self.asks.note_answer(str(msg.get("qid") or ""), msg)
        elif typ == "fetch":
            await self._handle_fetch(msg)
        elif typ == "fetch_result":
            req_id = str(msg.get("req_id") or "")
            fut = self._pending.pop(req_id, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
        elif typ == "error":
            log.warning("relay error: %s", msg)
            qid = str(msg.get("qid") or "")
            if qid:
                self.asks.note_error(qid, str(msg.get("code") or "relay_error"))

    async def _handle_query(self, msg: dict) -> None:
        asker = str(msg.get("asker") or "")
        if asker in ("", self.cfg.nick):
            return
        bucket = self._inbound_buckets.setdefault(asker, TokenBucket(rate=0.2, burst=2))
        if not bucket.allow():
            log.info("丢弃超额查询：%s", asker)
            return
        if self._semaphore.locked():
            return
        async with self._semaphore:
            started = time.monotonic()
            plan = msg.get("plan")
            try:
                if plan:
                    from ai.query_plan import evaluate_plan

                    hits = await asyncio.to_thread(evaluate_plan, self.searcher, plan, 8)
                    if not hits:
                        hits = await asyncio.to_thread(self.searcher.search, str(msg.get("text") or ""), 8)
                else:
                    hits = await asyncio.to_thread(self.searcher.search, str(msg.get("text") or ""), 8)
            except Exception:
                log.exception("本地检索失败（查询来自 %s）", asker)
                hits = []
            elapsed = int((time.monotonic() - started) * 1000)
            payload_hits = [h.to_dict() for h in hits]
            now = time.monotonic()
            allowed = self._allowed.setdefault(asker, {})
            for h in hits:
                allowed[int(h.doc_id)] = now + 600.0  # 10 分钟内可请求全文预览
            # 应答内容只含片段与出处；relay 只转发，不落盘
            await self.client.send(
                {
                    "type": "answer",
                    "asker": asker,
                    "qid": str(msg.get("qid") or ""),
                    "node": self.cfg.nick,
                    "elapsed_ms": elapsed,
                    "hits": payload_hits,
                }
            )

    async def _handle_fetch(self, msg: dict) -> None:
        """对端请求全文预览：仅限本机刚返回给该提问者的文档（10 分钟有效）。"""
        asker = str(msg.get("asker") or "")
        req_id = str(msg.get("req_id") or "")
        doc_id = int(msg.get("doc_id") or 0)
        if asker in ("", self.cfg.nick):
            return
        bucket = self._fetch_buckets.setdefault(asker, TokenBucket(rate=0.2, burst=4))
        if not bucket.allow():
            await self.client.send(
                {"type": "fetch_result", "to": asker, "req_id": req_id, "doc_id": doc_id,
                 "text": "", "error": "请求过于频繁"}
            )
            return
        allowed = self._allowed.get(asker, {})
        expiry = allowed.get(doc_id)
        if expiry is None or expiry < time.monotonic():
            await self.client.send(
                {"type": "fetch_result", "to": asker, "req_id": req_id, "doc_id": doc_id,
                 "text": "", "error": "该文档不在最近搜索结果中（或已过期）"}
            )
            return
        try:
            text = await asyncio.to_thread(self.searcher.get_doc_text, doc_id, 20000)
        except Exception as exc:
            log.exception("读取文档失败 doc_id=%s", doc_id)
            text = ""
            err = "读取失败：" + str(exc)
        else:
            err = "" if text else "文档为空或不可读"
        await self.client.send(
            {"type": "fetch_result", "to": asker, "req_id": req_id, "doc_id": doc_id,
             "text": (text or "")[:8000], "error": err}
        )

    async def fetch(self, node: str, doc_id: int, timeout_s: float = 15.0) -> str:
        """向对端请求全文预览（仅限其最近返回给本机的搜索结果）。"""
        import uuid

        if not self.connected:
            raise ValueError("relay 未连接")
        req_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            ok = await self.client.send(
                {"type": "fetch", "to": node, "asker": self.cfg.nick,
                 "req_id": req_id, "doc_id": int(doc_id)}
            )
            if not ok:
                raise ValueError("relay 发送失败")
            result = await asyncio.wait_for(fut, timeout_s)
        except asyncio.TimeoutError:
            raise ValueError("对端响应超时") from None
        finally:
            self._pending.pop(req_id, None)
        if result.get("error"):
            raise ValueError(str(result.get("error")))
        return str(result.get("text") or "")