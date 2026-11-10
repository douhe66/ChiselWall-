"""中心 relay（设计 §3/§4/§11，M4）：只做在线状态、问题广播、答案转发、限流。

实现选择（设计变更，记入 design §16）：原设计用 nats-server 二进制；为「M6 单包分发 +
内网免外部依赖」改为自建 WebSocket relay（Python/FastAPI，协议与其等价的 core 语义：
无持久化、at-most-once、只投递给在线连接）。消息协议见 docs/design/m4-relay-protocol.md。

不存储任何知识：只转发 JSON 消息；答案/问题仅在内存中路由。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger("chiselwall.relay")

ALL_GROUP = "全员组"
MAX_MESSAGE_BYTES = 256 * 1024


class TokenBucket:
    """单用户令牌桶：rate=每秒补充量，burst=桶容量。"""

    def __init__(self, rate: float = 0.2, burst: int = 2) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def allow(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass
class Client:
    user: str
    groups: list[str] = field(default_factory=lambda: [ALL_GROUP])
    last_seen: float = field(default_factory=time.monotonic)
    send: Optional[Callable[[dict], Awaitable[None]]] = None


class GroupStore:
    """组注册表持久化（JSON：name -> {description, created_by, created_at}）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取组注册表失败：%s", self.path)
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, groups: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class RelayHub:
    """在线状态 + 路由 + 限流 + 组注册表（纯逻辑，便于单测）。"""

    def __init__(
        self,
        users: dict[str, str],
        *,
        rate: float = 0.2,
        burst: int = 2,
        heartbeat_timeout: float = 45.0,
        group_store: Optional["GroupStore"] = None,
    ) -> None:
        self.users = dict(users)  # nick -> token
        self.rate = rate
        self.burst = burst
        self.heartbeat_timeout = heartbeat_timeout
        self.clients: dict[str, Client] = {}
        self.buckets: dict[str, TokenBucket] = {}
        self.group_store = group_store
        self.group_meta: dict[str, dict] = {}
        if group_store is not None:
            self.group_meta = group_store.load()
        self.group_meta.setdefault(
            ALL_GROUP, {"description": "默认全员组（所有在线节点）", "created_by": "system", "created_at": ""}
        )

    # ---- 鉴权与注册 ----
    def auth(self, user: str, token: str) -> bool:
        return bool(user) and self.users.get(user) == token

    def register(self, user: str, groups: list[str], send: Callable[[dict], Awaitable[None]]) -> dict:
        norm = [g.strip() for g in groups if str(g).strip()] or [ALL_GROUP]
        if ALL_GROUP not in norm:
            norm.append(ALL_GROUP)
        self.clients[user] = Client(user=user, groups=norm, send=send)
        self.buckets.setdefault(user, TokenBucket(self.rate, self.burst))
        for g in norm:  # 自由加入未定义的组 = 自动建组（无描述，创建者记为用户）
            self.ensure_group(g, creator=user)
        return {
            "type": "hello_ok",
            "user": user,
            "online": self.online(),
            "groups": self.groups_view(),
        }

    def unregister(self, user: str, send: Optional[Callable[[dict], Awaitable[None]]] = None) -> None:
        """注销连接；若该用户已被新连接替换（send 不同），不要误删新连接。"""
        cur = self.clients.get(user)
        if cur is None:
            return
        if send is not None and cur.send is not send:
            return
        self.clients.pop(user, None)

    def touch(self, user: str) -> None:
        c = self.clients.get(user)
        if c:
            c.last_seen = time.monotonic()

    def sweep(self, now: Optional[float] = None) -> list[str]:
        now = time.monotonic() if now is None else now
        dead = [u for u, c in self.clients.items() if now - c.last_seen > self.heartbeat_timeout]
        for u in dead:
            self.clients.pop(u, None)
        return dead

    def online(self) -> list[dict]:
        return [{"user": c.user, "groups": list(c.groups)} for c in self.clients.values()]

    def groups_view(self) -> list[dict]:
        """全部组（含无人加入的组）：名称 + 描述 + 在线人数，按名称排序。"""
        counts: dict[str, int] = {}
        members: dict[str, list[str]] = {}
        for c in self.clients.values():
            for g in c.groups:
                counts[g] = counts.get(g, 0) + 1
                members.setdefault(g, []).append(c.user)
        out = []
        for name in sorted(self.group_meta):
            meta = self.group_meta[name]
            out.append({
                "name": name,
                "description": meta.get("description") or "",
                "online": counts.get(name, 0),
                "members": sorted(members.get(name, [])),
            })
        return out

    def _persist_groups(self) -> None:
        if self.group_store is not None:
            try:
                self.group_store.save(self.group_meta)
            except OSError:
                log.exception("保存组注册表失败")

    def ensure_group(self, name: str, creator: str = "", description: str = "") -> None:
        name = (name or "").strip()
        if not name:
            return
        if name not in self.group_meta:
            self.group_meta[name] = {
                "description": description.strip(),
                "created_by": creator,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._persist_groups()

    def create_group(self, name: str, description: str, creator: str) -> dict:
        name = (name or "").strip()
        description = (description or "").strip()
        if not name or len(name) > 32:
            return {"ok": False, "error": "组名需为 1-32 个字符"}
        if len(description) > 200:
            return {"ok": False, "error": "组描述最多 200 字"}
        if name in self.group_meta:
            return {"ok": False, "error": "组已存在"}
        self.group_meta[name] = {
            "description": description,
            "created_by": creator,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._persist_groups()
        return {"ok": True, "name": name}

    # ---- 路由 ----
    def route_query(self, asker: str, groups: list[str]) -> list[Client]:
        gs = set(groups or [ALL_GROUP])
        all_group = ALL_GROUP in gs
        out: list[Client] = []
        for user, c in self.clients.items():
            if user == asker:
                continue
            if all_group or (gs & set(c.groups)):
                out.append(c)
        return out

    def allow_query(self, user: str) -> bool:
        bucket = self.buckets.setdefault(user, TokenBucket(self.rate, self.burst))
        return bucket.allow()

    async def deliver(self, conn: Client, payload: dict) -> None:
        if conn.send is None:
            return
        try:
            await conn.send(payload)
        except Exception:
            log.exception("relay deliver failed -> %s", conn.user)


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("relay 配置必须是 JSON 对象")
    users = data.get("users")
    if not isinstance(users, dict) or not users:
        raise ValueError("relay 配置缺少 users（nick -> token）")
    return data


def create_app(
    users: dict[str, str],
    *,
    rate: float = 0.2,
    burst: int = 2,
    groups_path: Optional[Path] = None,
    users_path: Optional[Path] = None,
) -> FastAPI:
    """组装 relay WebSocket 应用。groups_path/ users_path 用于持久化与热加载。"""
    app = FastAPI(title="ChiselWall Relay", version="0.4.0")
    store = GroupStore(groups_path) if groups_path is not None else None
    app.state.hub = RelayHub(users, rate=rate, burst=burst, group_store=store)
    app.state.users_path = users_path
    try:
        app.state.users_mtime = users_path.stat().st_mtime if users_path else 0.0
    except OSError:
        app.state.users_mtime = 0.0

    def _reload_users() -> None:
        """管理员改 relay.json 后无需重启：下一次 hello 自动加载。"""
        path = app.state.users_path
        if path is None:
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == app.state.users_mtime:
            return
        try:
            data = load_config(path)
        except (OSError, ValueError):
            log.exception("热加载 relay 用户配置失败")
            return
        app.state.hub.users = dict(data["users"])
        app.state.users_mtime = mtime
        log.info("relay 已热加载用户配置（%d 个用户）", len(app.state.hub.users))

    @app.get("/health")
    def health() -> dict:
        hub: RelayHub = app.state.hub
        return {"ok": True, "version": "0.4.0", "online": len(hub.clients)}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        hub: RelayHub = app.state.hub
        await ws.accept()
        user: Optional[str] = None

        async def send(payload: dict) -> None:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))

        try:
            while True:
                raw = await ws.receive_text()
                if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await send({"type": "error", "code": "message_too_large"})
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    await send({"type": "error", "code": "bad_json"})
                    continue
                typ = msg.get("type")

                if typ == "hello":
                    _reload_users()
                    nick = str(msg.get("user") or "")
                    token = str(msg.get("token") or "")
                    if not hub.auth(nick, token):
                        await send({"type": "error", "code": "auth_failed"})
                        await ws.close()
                        return
                    user = nick
                    prev = hub.clients.get(nick)
                    if prev is not None and prev.send is not send:
                        log.info("relay: user %s reconnected, replacing old connection", nick)
                    hello = hub.register(nick, list(msg.get("groups") or []), send)
                    await send(hello)
                    await hub.broadcast_online()
                    continue

                if user is None:
                    await send({"type": "error", "code": "hello_required"})
                    continue

                hub.touch(user)
                if typ == "presence":
                    continue
                if typ == "group_create":
                    result = hub.create_group(
                        str(msg.get("name") or ""),
                        str(msg.get("description") or ""),
                        user,
                    )
                    if not result.get("ok"):
                        await send({"type": "error", "code": "group_create_failed",
                                    "message": result.get("error") or ""})
                    else:
                        await send({"type": "group_ok", "name": result.get("name")})
                        await hub.broadcast_online()
                    continue
                if typ == "query":
                    if not hub.allow_query(user):
                        await send({"type": "error", "code": "rate_limited", "qid": msg.get("qid")})
                        continue
                    qid = str(msg.get("qid") or "")
                    groups = [str(g) for g in (msg.get("groups") or [])]
                    targets = hub.route_query(user, groups)
                    forward = {
                        "type": "query",
                        "qid": qid,
                        "asker": user,
                        "text": str(msg.get("text") or ""),
                        "deadline_ms": int(msg.get("deadline_ms") or 5000),
                        "groups": groups,
                    }
                    for c in targets:
                        await hub.deliver(c, forward)
                    await send({"type": "query_ack", "qid": qid, "targets": len(targets)})
                    continue
                if typ == "answer":
                    asker = str(msg.get("asker") or "")
                    target = hub.clients.get(asker)
                    if target is not None:
                        await hub.deliver(target, msg)
                    continue
                if typ == "fetch":
                    # 全文预览请求：转发给目标节点（relay 不存内容）
                    target = hub.clients.get(str(msg.get("to") or ""))
                    forward = {
                        "type": "fetch",
                        "asker": user,
                        "req_id": str(msg.get("req_id") or ""),
                        "doc_id": int(msg.get("doc_id") or 0),
                    }
                    if target is None:
                        await send({
                            "type": "fetch_result",
                            "to": user,
                            "req_id": forward["req_id"],
                            "doc_id": forward["doc_id"],
                            "text": "",
                            "error": "对方不在线",
                        })
                    else:
                        await hub.deliver(target, forward)
                    continue
                if typ == "fetch_result":
                    target = hub.clients.get(str(msg.get("to") or ""))
                    if target is not None:
                        await hub.deliver(target, msg)
                    continue
                await send({"type": "error", "code": "unknown_type"})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("relay ws error")
        finally:
            if user is not None:
                hub.unregister(user, send)
                try:
                    await hub.broadcast_online()
                except Exception:
                    pass

    # 在线列表广播（内部辅助，挂到 hub 上便于测试）
    async def broadcast_online() -> None:
        payload = {"type": "online", "online": app.state.hub.online(), "groups": app.state.hub.groups_view()}
        for c in list(app.state.hub.clients.values()):
            await app.state.hub.deliver(c, payload)

    app.state.hub.broadcast_online = broadcast_online  # type: ignore[attr-defined]
    return app


def main(argv: Optional[list[str]] = None) -> None:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "admin" in argv:  # 打包后同一 exe：chiselwall-relay.exe [--config x] admin invite 张三
        idx = argv.index("admin")
        from relay.admin import main as admin_main

        admin_main(argv[:idx] + argv[idx + 1:])
        return
    parser = argparse.ArgumentParser(description="ChiselWall relay（只路由，不存知识）")
    parser.add_argument("--config", default="relay.json", help="relay 配置（users: nick->token）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18760)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--init", action="store_true", help="生成样例 relay.json（不存在时）并退出")
    args = parser.parse_args(argv)

    if args.init:
        path = Path(args.config)
        if path.exists():
            print("配置已存在：%s" % path)
            return
        sample = {"users": {}, "rate": 0.2, "burst": 2}
        path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已生成 %s。下一步用 admin 命令发邀请：" % path)
        print("  chiselwall-relay.exe admin invite 张三 --group 全员组 --url ws://<本机IP>:18760/ws --config %s" % path)
        return

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)
    groups_path = cfg_path.with_name(cfg_path.stem + "-groups.json")
    app = create_app(
        dict(cfg["users"]),
        rate=float(cfg.get("rate", 0.2)),
        burst=int(cfg.get("burst", 2)),
        groups_path=groups_path,
        users_path=cfg_path,
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()