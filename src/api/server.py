"""本地 HTTP API（设计 §11）：仅监听 127.0.0.1 + token 鉴权（评审结论③ P0）。

token：首启随机生成（secrets.token_urlsafe(24)），持久化在 ~/.chiselwall/config.json，
控制台打印一次；请求头 X-Chisel-Token 校验；/api/health 与静态页面公开
（健康检查不含知识内容）。

打开文件（设计 §9 双平台写法）：
    打开所在位置：Windows explorer /select,path（高亮选中）；
                  Linux 优先 DBus org.freedesktop.FileManager1.ShowItems（高亮选中），
                  无 DBus 回退 xdg-open 打开所在目录；
    直接打开：Windows os.startfile；Linux xdg-open。
    两者只接受「已索引且此刻未被排除」的文档（红线⑤）。
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# UI 静态目录（源码：src/ui；打包：_MEIPASS/ui），供 app.mount 使用
def _resolve_ui_dir() -> Path:
    base = Path(__file__).resolve().parents[1]
    for candidate in (base / "ui", base / "src" / "ui"):
        if (candidate / "index.html").exists():
            return candidate
    return base / "ui"


UI_DIR = _resolve_ui_dir()


def _sse_response(events):
    """把 {type, ...} 事件转成 SSE 流；生成器异常也转成 error 事件。"""

    def gen():
        try:
            for ev in events:
                payload = json.dumps(ev, ensure_ascii=False, default=str)
                yield "event: %s\ndata: %s\n\n" % (ev.get("type", "message"), payload)
        except Exception as exc:
            yield "event: error\ndata: %s\n\n" % json.dumps(
                {"type": "error", "error": str(exc)}, ensure_ascii=False
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# 版本号单一来源（M2-FIX5）：同时用于
#   - index.html 中 app.js/style.css 的 ?v= 缓存破坏参数（占位符 __CHISEL_VERSION__）
#   - 页脚版本展示
#   - /api/health 的 version 字段
# 任何 UI 改动必须 bump 此值，否则用户浏览器仍可能命中旧缓存。
APP_VERSION = "0.9.29"
log = logging.getLogger("chiselwall.api")

# 健康检查路径：公开（不含知识内容）
PUBLIC_PATHS = {"/api/health", "/"}


class MountIn(BaseModel):
    path: str
    kind: Optional[str] = None


class MountPatchIn(BaseModel):
    enabled: Optional[bool] = None
    sensitivity_ignored: Optional[bool] = None


class GroupPatchIn(BaseModel):
    sensitivity_ignored: Optional[bool] = None


class DocShareIn(BaseModel):
    state: str


class SensitivityRuleIn(BaseModel):
    label: str
    pattern: str
    category: str = ""
    severity: str = "medium"


class SensitivityRulePatchIn(BaseModel):
    enabled: bool


class SensitivityIgnoreIn(BaseModel):
    ignored: bool


class SensitivityDocRuleIgnoreIn(BaseModel):
    rule_id: str


class RuleIn(BaseModel):
    kind: str
    pattern: str


class AIConfigIn(BaseModel):
    """AI 配置更新（M5）：api_key 缺省/空表示保持不变。"""
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    enabled: Optional[bool] = None
    use_system_proxy: Optional[bool] = None
    system_prompt: Optional[str] = None
    max_context_chars: Optional[int] = None
    top_n: Optional[int] = None
    max_tokens: Optional[int] = None
    mode: Optional[str] = None
    placeholder_files: Optional[dict[str, str]] = None
    headers: Optional[list[dict[str, str]]] = None


class QueryConfigIn(BaseModel):
    enabled: Optional[bool] = None
    max_queries: Optional[int] = None
    timeout_s: Optional[float] = None
    system_prompt: Optional[str] = None


class RerankConfigIn(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_style: Optional[str] = None
    model_dir: Optional[str] = None
    max_candidates: Optional[int] = None
    top_k: Optional[int] = None
    use_system_proxy: Optional[bool] = None
    idle_unload_s: Optional[int] = None
    cache_size: Optional[int] = None


class VectorConfigIn(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    model_dir: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    dim: Optional[int] = None
    top_k: Optional[int] = None
    use_system_proxy: Optional[bool] = None
    min_similarity: Optional[float] = None
    vector_only_max: Optional[int] = None
    require_lexical: Optional[bool] = None
    backend: Optional[str] = None


class RelayConfigIn(BaseModel):
    relay_url: Optional[str] = None
    nick: Optional[str] = None
    token: Optional[str] = None
    groups: Optional[list[str]] = None
    enabled: Optional[bool] = None


class RelayGroupIn(BaseModel):
    """新建提问组（M6）：组名 + 描述（描述会展示给所有人）。"""
    name: str
    description: str = ""


class RelayTestIn(BaseModel):
    relay_url: Optional[str] = None
    nick: Optional[str] = None
    token: Optional[str] = None
    groups: Optional[list[str]] = None


class FetchIn(BaseModel):
    """远端全文预览请求（M4.1）：node=目标节点昵称，doc_id=对方文档 id。"""
    node: str
    doc_id: int


class SessionIn(BaseModel):
    title: Optional[str] = None


class SessionAskIn(BaseModel):
    text: str
    summarize: bool = True


class CompleteIn(BaseModel):
    turn_id: int
    qid: str


class AskIn(BaseModel):
    """随手问请求体（M4）。注：模型必须定义在模块级——函数内定义的模型 +
    `from __future__ import annotations` 会让 FastAPI 无法解析注解，误判为 query 参数。"""
    text: str
    groups: Optional[list[str]] = None


class GroupIn(BaseModel):
    name: str


class MemberIn(BaseModel):
    mount_id: int
    rel_path: str = ""


def load_or_create_token(config_dir: Path) -> str:
    """读取或生成本地 API token（评审结论③ P0）。"""
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_dir / "config.json"
    cfg: dict = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    token = str(cfg.get("api_token") or "")
    if not token:
        token = secrets.token_urlsafe(24)
        cfg["api_token"] = token
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return token


def create_app(agent, token: str = "") -> FastAPI:
    """组装 FastAPI 应用。token 为空表示不启用鉴权（仅测试用）。"""
    app = FastAPI(title="ChiselWall Node", version=APP_VERSION)

    @app.on_event("startup")
    async def _startup() -> None:
        log.info("API starting version=%s auth=%s", APP_VERSION, "enabled" if token else "disabled")
        # M4：在事件循环内启动 relay 长连接（未配置时无操作）
        try:
            await agent.start_bus()
        except Exception:
            log.exception("relay bus 启动失败（本地功能不受影响）")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        log.info("API stopping")
        try:
            await agent.stop_bus()
        except Exception:
            log.exception("relay bus 停止失败")

    def require_token(x_chisel_token: Optional[str] = Header(default=None)) -> None:
        if token and x_chisel_token != token:
            raise HTTPException(status_code=401, detail="invalid token")

    # ---- 查询与统计 ----
    @app.get("/api/health")
    def health() -> dict:
        # F4：不暴露本机路径（隐私）；version 与 APP_VERSION 单一来源
        return {"ok": True, "version": APP_VERSION}

    @app.get("/api/stats", dependencies=[Depends(require_token)])
    def stats() -> dict:
        return agent.db.stats()

    @app.get("/api/search", dependencies=[Depends(require_token)])
    def search(q: str = "", topk: int = 8) -> dict:
        topk = max(1, min(topk, 50))
        return {"query": q, "hits": agent.search(q, topk)}

    # ---- 挂载管理 ----
    @app.get("/api/mounts", dependencies=[Depends(require_token)])
    def list_mounts() -> list:
        out = []
        for m in agent.db.list_mounts():
            n = agent.db.query(
                "SELECT COUNT(*) AS n FROM docs WHERE mount_id=?", (m.mount_id,)
            )[0]["n"]
            out.append({"mount_id": m.mount_id, "kind": m.kind, "path": m.path, "docs": n, "enabled": bool(m.enabled), "sensitivity_ignored": bool(m.sensitivity_ignored)})
        return out

    @app.post("/api/mounts", dependencies=[Depends(require_token)])
    def add_mount(body: MountIn) -> dict:
        try:
            return agent.add_mount(body.path, body.kind)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.patch("/api/mounts/{mount_id}", dependencies=[Depends(require_token)])
    def patch_mount(mount_id: int, body: MountPatchIn) -> dict:
        try:
            result: dict = {"ok": True, "mount_id": mount_id}
            if body.enabled is not None:
                result.update(agent.set_mount_enabled(mount_id, body.enabled))
            if body.sensitivity_ignored is not None:
                agent.sensitivity.set_mount_ignored(mount_id, body.sensitivity_ignored)
                result["sensitivity_ignored"] = bool(body.sensitivity_ignored)
            if body.enabled is None and body.sensitivity_ignored is None:
                raise HTTPException(status_code=400, detail="没有要更新的字段")
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.delete("/api/mounts/{mount_id}", dependencies=[Depends(require_token)])
    def remove_mount(mount_id: int) -> dict:
        if agent.db.get_mount(mount_id) is None:
            raise HTTPException(status_code=404, detail="mount not found")
        agent.remove_mount(mount_id)
        return {"ok": True}

    # ---- 排除规则 ----
    @app.get("/api/excludes", dependencies=[Depends(require_token)])
    def list_rules() -> list:
        return [
            {"rule_id": i, "kind": k, "pattern": p}
            for i, k, p in agent.db.list_exclude_rules()
        ]

    @app.post("/api/excludes", dependencies=[Depends(require_token)])
    def add_rule(body: RuleIn) -> dict:
        try:
            return agent.add_exclude_rule(body.kind, body.pattern)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/excludes/{rule_id}", dependencies=[Depends(require_token)])
    def remove_rule(rule_id: int) -> dict:
        agent.remove_exclude_rule(rule_id)
        return {"ok": True}

    # ---- 文档查看与打开 ----
    @app.get("/api/docs/{doc_id}", dependencies=[Depends(require_token)])
    def doc_text(doc_id: int, max_chars: int = 20000) -> dict:
        try:
            return {"doc_id": doc_id, "text": agent.get_doc_text(doc_id, max_chars)}
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.patch("/api/docs/{doc_id}/sharing", dependencies=[Depends(require_token)])
    def patch_doc_sharing(doc_id: int, body: DocShareIn) -> dict:
        try:
            return agent.set_doc_share(doc_id, body.state)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/docs/{doc_id}/open-location", dependencies=[Depends(require_token)])
    def open_location(doc_id: int) -> dict:
        try:
            ok, how, hint = agent.open_location(doc_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": ok, "how": how, "hint": hint}

    @app.get("/api/docs/{doc_id}/open", dependencies=[Depends(require_token)])
    def open_file(doc_id: int) -> dict:
        try:
            ok, how, hint, alternatives = agent.open_file(doc_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": ok, "how": how, "hint": hint, "alternatives": alternatives}

    @app.get("/api/docs/{doc_id}/open-with", dependencies=[Depends(require_token)])
    def open_with(doc_id: int, app: str = "") -> dict:
        """用指定应用打开（M2-FIX6）：app ∈ {vscode, notepad}。"""
        try:
            ok, how, hint = agent.open_with(doc_id, app)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": ok, "how": how, "hint": hint}

    # ---- 笔记分组（设计 §15.6；M3-2） ----
    @app.get("/api/groups", dependencies=[Depends(require_token)])
    def list_groups() -> list:
        out = []
        for g in agent.db.list_groups():
            g["docs"] = agent.db.group_doc_count(g["group_id"])
            out.append(g)
        return out

    @app.post("/api/groups", dependencies=[Depends(require_token)])
    def add_group(body: GroupIn) -> dict:
        try:
            return agent.add_group(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.patch("/api/groups/{group_id}", dependencies=[Depends(require_token)])
    def patch_group(group_id: int, body: GroupPatchIn) -> dict:
        try:
            if body.sensitivity_ignored is None:
                raise HTTPException(status_code=400, detail="没有要更新的字段")
            agent.sensitivity.set_group_ignored(group_id, body.sensitivity_ignored)
            return {"ok": True, "group_id": group_id, "sensitivity_ignored": bool(body.sensitivity_ignored)}
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.delete("/api/groups/{group_id}", dependencies=[Depends(require_token)])
    def remove_group(group_id: int) -> dict:
        try:
            agent.remove_group(group_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True}

    @app.get("/api/groups/{group_id}/members", dependencies=[Depends(require_token)])
    def list_members(group_id: int) -> list:
        return agent.db.list_group_members(group_id)

    @app.post("/api/groups/{group_id}/members", dependencies=[Depends(require_token)])
    def add_member(group_id: int, body: MemberIn) -> dict:
        try:
            return agent.add_group_member(group_id, body.mount_id, body.rel_path)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.delete("/api/groups/{group_id}/members/{member_id}", dependencies=[Depends(require_token)])
    def remove_member(group_id: int, member_id: int) -> dict:
        agent.remove_group_member(member_id)
        return {"ok": True}

    @app.post("/api/groups/{group_id}/exclude", dependencies=[Depends(require_token)])
    def exclude_group(group_id: int) -> dict:
        """整组批量排除：生成真实 exclude_rules 记录（GR-07 裁决）。"""
        try:
            return agent.exclude_group(group_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # ---- 敏感词检查（L0+L1） ----
    @app.get("/api/sensitivity/summary", dependencies=[Depends(require_token)])
    def sensitivity_summary() -> dict:
        return agent.sensitivity.summary()

    @app.get("/api/sensitivity/rules", dependencies=[Depends(require_token)])
    def sensitivity_rules() -> dict:
        return {"rules": agent.sensitivity.rules()}

    @app.post("/api/sensitivity/rules", dependencies=[Depends(require_token)])
    def add_sensitivity_rule(body: SensitivityRuleIn) -> dict:
        try:
            return agent.sensitivity.add_custom_rule(
                body.label, body.pattern, body.category, body.severity
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.patch("/api/sensitivity/rules/{rule_id}", dependencies=[Depends(require_token)])
    def patch_sensitivity_rule(rule_id: str, body: SensitivityRulePatchIn) -> dict:
        try:
            return agent.sensitivity.set_rule_enabled(rule_id, body.enabled)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.delete("/api/sensitivity/rules/{rule_id}", dependencies=[Depends(require_token)])
    def delete_sensitivity_rule(rule_id: str) -> dict:
        try:
            agent.sensitivity.delete_custom_rule(rule_id)
            return {"ok": True}
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/sensitivity/rescan", dependencies=[Depends(require_token)])
    def sensitivity_rescan() -> dict:
        agent.sensitivity.rescan_async()
        return {"ok": True, "scanning": agent.sensitivity.scanning()}

    @app.get("/api/sensitivity/pending", dependencies=[Depends(require_token)])
    def sensitivity_pending(limit: int = 100) -> dict:
        limit = max(1, min(limit, 500))
        return {"docs": agent.sensitivity.pending(limit=limit)}

    @app.get("/api/sensitivity/docs/{doc_id}", dependencies=[Depends(require_token)])
    def sensitivity_doc(doc_id: int) -> dict:
        try:
            return agent.sensitivity.doc_detail(doc_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/sensitivity/docs/{doc_id}/ack", dependencies=[Depends(require_token)])
    def sensitivity_ack(doc_id: int) -> dict:
        try:
            return agent.sensitivity.ack_doc(doc_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.patch("/api/sensitivity/docs/{doc_id}/ignore", dependencies=[Depends(require_token)])
    def sensitivity_doc_ignore(doc_id: int, body: SensitivityIgnoreIn) -> dict:
        try:
            return agent.sensitivity.set_doc_ignored(doc_id, body.ignored)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/sensitivity/docs/{doc_id}/ignore-rule", dependencies=[Depends(require_token)])
    def sensitivity_doc_ignore_rule(doc_id: int, body: SensitivityDocRuleIgnoreIn) -> dict:
        try:
            return agent.sensitivity.ignore_rule_for_doc(doc_id, body.rule_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # ---- 文档列表与排除明细（M3-2：观察-1 双列表） ----
    @app.get("/api/docs", dependencies=[Depends(require_token)])
    def list_docs(
        mount_id: Optional[int] = None,
        group_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> list:
        limit = max(1, min(limit, 2000))
        docs = agent.db.list_docs(status=status, limit=limit, mount_id=mount_id, group_id=group_id)
        return agent.sensitivity.decorate_docs(docs)

    @app.get("/api/excluded", dependencies=[Depends(require_token)])
    def excluded_docs() -> list:
        """「排除 / 不支持格式」明细列表（观察-1：与解析失败列表分开展示）。"""
        return agent.db.excluded_docs(limit=500)

    async def _run_ai_generation(item, text: str, aggregated: list) -> None:
        """AI 综合生成（后台任务）：不阻塞结果轮询。"""
        import asyncio

        try:
            item.ai = await asyncio.to_thread(agent.ai_answer, text, aggregated)
        except Exception as exc:
            item.ai = {
                "status": "error",
                "answer": "",
                "citations": [],
                "model": agent.ai_config.model,
                "elapsed_ms": 0,
                "context_chars": 0,
                "error": str(exc),
            }

    # ---- 随手问（M4：relay 广播问答） ----
    @app.get("/api/relay/config", dependencies=[Depends(require_token)])
    def relay_config() -> dict:
        return agent.relay_public()

    @app.post("/api/relay/config", dependencies=[Depends(require_token)])
    async def relay_config_save(body: RelayConfigIn) -> dict:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        saved = agent.save_relay(data)
        try:
            await agent.restart_bus()
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Relay 重连失败: " + str(exc))
        return agent.relay_public()

    @app.post("/api/relay/groups", dependencies=[Depends(require_token)])
    async def relay_group_create(body: RelayGroupIn) -> dict:
        try:
            return await agent.create_group(body.name, body.description)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/relay", dependencies=[Depends(require_token)])
    def relay_info() -> dict:
        return agent.bus.info()

    @app.post("/api/relay/test", dependencies=[Depends(require_token)])
    async def relay_test(body: Optional[RelayTestIn] = None) -> dict:
        try:
            return await agent.test_relay(body.model_dump() if body else {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/ask", dependencies=[Depends(require_token)])
    async def ask(body: AskIn) -> dict:
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问题不能为空")
        try:
            return await agent.bus.ask(text, body.groups)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ask/{qid}", dependencies=[Depends(require_token)])
    def ask_view(qid: str) -> dict:
        view = agent.bus.view(qid)
        if view is None:
            raise HTTPException(status_code=404, detail="提问不存在或已过期")
        item = agent.bus.asks.items.get(qid)
        if item is not None and not item.display_enriched and view["aggregated"]:
            view["aggregated"] = agent.enrich_display(view["aggregated"])
            item.display_enriched = True
        if item is not None and not item.reranked and view["aggregated"]:
            view["aggregated"] = agent.rerank_hits(view["text"], view["aggregated"])
            item.reranked = True
        return view

    @app.post("/api/ask/{qid}/followup", dependencies=[Depends(require_token)])
    async def ask_followup(qid: str, body: SessionAskIn) -> dict:
        import asyncio

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="追问不能为空")
        result = await asyncio.to_thread(agent.ask_followup, qid, text)
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return result

    # ---- M8 随手问会话 ----
    @app.get("/api/ask-sessions", dependencies=[Depends(require_token)])
    def ask_sessions() -> list:
        return agent.ask_session_list()

    @app.post("/api/ask-sessions", dependencies=[Depends(require_token)])
    def ask_session_create(body: SessionIn) -> dict:
        return agent.ask_session_create(body.title or "")

    @app.get("/api/ask-sessions/{sid}", dependencies=[Depends(require_token)])
    def ask_session_get(sid: str) -> dict:
        result = agent.ask_session_get(sid)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "会话不存在")
        return result

    @app.patch("/api/ask-sessions/{sid}", dependencies=[Depends(require_token)])
    def ask_session_rename(sid: str, body: SessionIn) -> dict:
        result = agent.ask_session_rename(sid, body.title or "")
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="会话不存在")
        return result

    @app.delete("/api/ask-sessions/{sid}", dependencies=[Depends(require_token)])
    def ask_session_delete(sid: str) -> dict:
        result = agent.ask_session_delete(sid)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="会话不存在")
        return result

    @app.post("/api/ask-sessions/{sid}/ask", dependencies=[Depends(require_token)])
    async def ask_session_ask(sid: str, body: AskIn) -> dict:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="问题不能为空")
        if not body.groups:
            raise HTTPException(status_code=400, detail="至少选择一个提问组")
        try:
            return await agent.ask_session_ask(sid, text, body.groups)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/ask-sessions/{sid}/complete", dependencies=[Depends(require_token)])
    async def ask_session_complete(sid: str, body: CompleteIn) -> dict:
        import asyncio

        result = await asyncio.to_thread(agent.ask_session_complete, sid, body.turn_id, body.qid)
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return result

    @app.post("/api/ask-sessions/{sid}/summarize", dependencies=[Depends(require_token)])
    async def ask_session_summarize(sid: str) -> dict:
        import asyncio

        if not agent.ai_config.ready:
            raise HTTPException(status_code=400, detail="AI 未配置：请先在设置页填写 base_url / model")
        await agent.resolve_ai_evidence(sid)
        result = await asyncio.to_thread(agent.ask_session_summarize, sid)
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return result

    @app.post("/api/ask-sessions/{sid}/summarize/stream", dependencies=[Depends(require_token)])
    async def ask_session_summarize_stream(sid: str):
        if not agent.ai_config.ready:
            raise HTTPException(status_code=400, detail="AI 未配置：请先在设置页填写 base_url / model")
        await agent.resolve_ai_evidence(sid)
        return _sse_response(agent.ask_session_summarize_stream(sid))

    @app.post("/api/ask-sessions/{sid}/research", dependencies=[Depends(require_token)])
    async def ask_session_research(sid: str) -> dict:
        if not agent.ai_config.ready:
            raise HTTPException(status_code=400, detail="AI 未配置：请先在设置页填写 base_url / model")
        result = await agent.research_answer(sid)
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return result

    @app.post("/api/ask-sessions/{sid}/followup/stream", dependencies=[Depends(require_token)])
    async def ask_session_followup_stream(sid: str, body: SessionAskIn):
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="追问不能为空")
        if body.summarize and not agent.ai_config.ready:
            raise HTTPException(status_code=400, detail="AI 未配置：请先在设置页填写 base_url / model")
        if body.summarize:
            await agent.resolve_ai_evidence(sid)
        return _sse_response(agent.ask_session_followup_stream(sid, text, body.summarize))

    @app.post("/api/ask-sessions/{sid}/followup", dependencies=[Depends(require_token)])
    async def ask_session_followup(sid: str, body: SessionAskIn) -> dict:
        import asyncio

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="追问不能为空")
        if body.summarize:
            await agent.resolve_ai_evidence(sid)
        result = await asyncio.to_thread(agent.ask_session_followup, sid, text, body.summarize)
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return result

    # ---- 向量混合检索（M7） ----
    @app.get("/api/vector/config", dependencies=[Depends(require_token)])
    def vector_config() -> dict:
        return agent.vector_public()

    @app.post("/api/vector/config", dependencies=[Depends(require_token)])
    def vector_config_save(body: VectorConfigIn) -> dict:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        return agent.save_vector(data)

    @app.get("/api/vector/status", dependencies=[Depends(require_token)])
    def vector_status() -> dict:
        return agent.vector_status()

    @app.post("/api/vector/rebuild", dependencies=[Depends(require_token)])
    async def vector_rebuild() -> dict:
        import asyncio

        try:
            result = await asyncio.to_thread(agent.vector_rebuild)
        except Exception as exc:
            import logging

            logging.getLogger("chiselwall.api").exception("重建向量索引失败")
            raise HTTPException(status_code=500, detail="重建向量索引失败: " + str(exc))
        result["status"] = agent.vector_public()
        return result

    # ---- 查询改写（P2） ----
    @app.get("/api/query/config", dependencies=[Depends(require_token)])
    def query_config() -> dict:
        return agent.query_public()

    @app.post("/api/query/config", dependencies=[Depends(require_token)])
    def query_config_save(body: QueryConfigIn) -> dict:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        return agent.save_query(data)

    @app.post("/api/query/test", dependencies=[Depends(require_token)])
    async def query_test(body: AskIn) -> dict:
        import asyncio

        text = (body.text or "").strip()
        variants = await asyncio.to_thread(agent.rewriter.rewrite, text)
        return {"ready": agent.rewriter.ready, "variants": variants, "raw": getattr(agent.rewriter, "last_raw", "")}

    # ---- 重排（P1） ----
    @app.get("/api/rerank/config", dependencies=[Depends(require_token)])
    def rerank_config() -> dict:
        return agent.rerank_public()

    @app.post("/api/rerank/config", dependencies=[Depends(require_token)])
    def rerank_config_save(body: RerankConfigIn) -> dict:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        return agent.save_rerank(data)

    @app.post("/api/rerank/test", dependencies=[Depends(require_token)])
    async def rerank_test() -> dict:
        import asyncio

        try:
            return await asyncio.to_thread(agent.rerank_test)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ---- AI 入口（M5，设计 §10） ----
    @app.get("/api/ai/config", dependencies=[Depends(require_token)])
    def ai_config() -> dict:
        return agent.ai_public()

    @app.post("/api/ai/config", dependencies=[Depends(require_token)])
    def ai_config_save(body: AIConfigIn) -> dict:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        return agent.update_ai(data)

    @app.post("/api/fetch", dependencies=[Depends(require_token)])
    async def fetch_remote(body: FetchIn) -> dict:
        """向对端节点请求全文预览（仅限其最近返回给本机的搜索结果）。"""
        try:
            text = await agent.bus.fetch(body.node, int(body.doc_id))
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"ok": True, "text": text}

    @app.post("/api/ai/test", dependencies=[Depends(require_token)])
    async def ai_test(body: Optional[AIConfigIn] = None) -> dict:
        """连通性测试：body 可带表单未保存的值，测试不落盘。"""
        import asyncio

        overrides = {k: v for k, v in (body.model_dump() if body else {}).items() if v is not None}
        try:
            result = await asyncio.to_thread(agent.ai_ping, overrides)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="模型连通性测试失败: " + str(exc))
        return {"ok": True, "model": result.get("model"), "elapsed_ms": result.get("elapsed_ms")}

    @app.post("/api/ai/ask", dependencies=[Depends(require_token)])
    async def ai_ask(body: AskIn) -> dict:
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问题不能为空")
        if not agent.ai_config.ready:
            raise HTTPException(
                status_code=400,
                detail="AI 未配置：请先在设置页填写 base_url / model（以及 api_key）",
            )
        try:
            return await agent.bus.ask(text, body.groups)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ai/ask/{qid}", dependencies=[Depends(require_token)])
    async def ai_ask_view(qid: str) -> dict:
        import asyncio

        view = agent.bus.view(qid)
        if view is None:
            raise HTTPException(status_code=404, detail="提问不存在或已过期")
        import asyncio

        item = agent.bus.asks.items.get(qid)
        if item is not None and not item.display_enriched and view["aggregated"]:
            view["aggregated"] = agent.enrich_display(view["aggregated"])
            item.display_enriched = True
        if item is not None and not item.reranked and view["aggregated"]:
            view["aggregated"] = agent.rerank_hits(view["text"], view["aggregated"])
            item.reranked = True
        ai: Optional[dict] = None
        if item is not None:
            if item.ai is None and not item.ai_started and view["status"] in ("done", "error"):
                item.ai_started = True
                # 后台生成：先返回当前结果，AI 完成后下一次轮询再展示（逐条结果不被阻塞）
                asyncio.create_task(_run_ai_generation(item, view["text"], view["aggregated"]))
            ai = item.ai
            if ai is None and item.ai_started:
                ai = {"status": "generating", "answer": "", "citations": [], "error": ""}
        return {"view": view, "ai": ai}

    # ---- M8 AI 会话 ----
    @app.get("/api/ai/sessions", dependencies=[Depends(require_token)])
    def ai_sessions() -> list:
        return agent.ai_session_list()

    @app.post("/api/ai/sessions", dependencies=[Depends(require_token)])
    def ai_session_create(body: SessionIn) -> dict:
        return agent.ai_session_create(body.title or "")

    @app.get("/api/ai/sessions/{sid}", dependencies=[Depends(require_token)])
    def ai_session_get(sid: str) -> dict:
        result = agent.ai_session_get(sid)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "会话不存在")
        return result

    @app.patch("/api/ai/sessions/{sid}", dependencies=[Depends(require_token)])
    def ai_session_rename(sid: str, body: SessionIn) -> dict:
        result = agent.ai_session_rename(sid, body.title or "")
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="会话不存在")
        return result

    @app.delete("/api/ai/sessions/{sid}", dependencies=[Depends(require_token)])
    def ai_session_delete(sid: str) -> dict:
        result = agent.ai_session_delete(sid)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="会话不存在")
        return result

    @app.post("/api/ai/sessions/{sid}/ask", dependencies=[Depends(require_token)])
    async def ai_session_ask(sid: str, body: SessionAskIn) -> dict:
        import asyncio

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="问题不能为空")
        if not agent.ai_config.ready:
            raise HTTPException(status_code=400, detail="AI 未配置：请先在设置页填写 base_url / model")
        result = await asyncio.to_thread(agent.ai_session_ask, sid, text)
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return result

    # ---- 失败列表 / 重建 ----
    @app.get("/api/failed", dependencies=[Depends(require_token)])
    def failed() -> list:
        return agent.db.failed_docs(limit=200)

    @app.post("/api/reindex", dependencies=[Depends(require_token)])
    def reindex() -> dict:
        agent.reindex_all()
        return {"ok": True}

    # ---- 静态资源统一 no-cache（M2-FIX5 根因：浏览器旧缓存致 UI 修复不可见） ----
    # 注：本环境 Starlette 新版 StaticFiles 无 headers 参数，用中间件实现
    @app.middleware("http")
    async def _no_cache_static(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.middleware("http")
    async def _log_slow_or_failed(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("API failed %s %s", request.method, request.url.path)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 500 or elapsed_ms >= 1500:
            log.warning("API %s %s -> %s %.0fms", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    # ---- 首页（版本注入 + 缓存破坏，M2-FIX5） ----
    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        """index.html 里的 __CHISEL_VERSION__ 占位符由 APP_VERSION 注入。"""
        html_path = UI_DIR / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="ui not found")
        body = html_path.read_text(encoding="utf-8").replace("__CHISEL_VERSION__", APP_VERSION)
        return HTMLResponse(content=body, headers={"Cache-Control": "no-cache"})

    # 静态资源最后挂载（先匹配已注册的 /api/* 与 / 路由）
    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
    return app
