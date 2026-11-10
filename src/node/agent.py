"""Node Agent 组装（设计 §3/§13 目录规划）：连接器 + 索引 + 监视 + 检索 + API 入口。

M2 只组装本地核心（relay/广播为 M4）。启动即：加载挂载与排除规则 -> 每个挂载
起 watchdog 观察器 + 入队一次全量校验 -> 单线程低优先级 worker 后台处理。

红线②：拒绝把索引库所在目录挂载进去（索引库自吞）；红线⑤：排除规则新增立即
摘除已索引文档的 chunk；全文查看/打开前再次复核排除状态。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from connectors.base import Connector
from connectors.directory import DirectoryConnector
from connectors.excludes import ExcludeRule, ExclusionEngine
from indexer.chunker import Chunker
from indexer.db import IndexDB, MountRecord
from indexer.indexer import Indexer
from indexer.resources import CPU_THRESHOLD_DEFAULT
from indexer.watcher import DEBOUNCE_DEFAULT, WatchService
from searcher.search import Searcher

log = logging.getLogger("chiselwall.agent")

DEFAULT_CONFIG_DIR: Path = Path.home() / ".chiselwall"


@dataclass
class Config:
    db_path: Path
    config_dir: Path
    debounce: float = DEBOUNCE_DEFAULT
    cpu_threshold: float = CPU_THRESHOLD_DEFAULT
    chunk_min: int = 500
    chunk_max: int = 800
    chunk_overlap: int = 50
    max_chunks: int = 2000
    text_cap_chars: int = 5_000_000
    max_file_size: int = 20 * 1024 * 1024
    trigram_enabled: bool = False        # F2：trigram 兜底默认关闭，可配
    trigram_max_chars: int = 100_000     # F2：仅小文档写 trigram
    # M4 relay（随手问）：中心只路由；未配置时本地功能不受影响
    relay_enabled: bool = False
    relay_url: str = ""                  # ws://<relay>:18760/ws
    relay_nick: str = "我"
    relay_groups: list[str] = None       # type: ignore[assignment]
    relay_token: str = ""
    ask_deadline_ms: int = 5000
    answer_budget_ms: int = 800
    # M5 AI（OpenAI 兼容；CLI 覆盖 ai.json，未配置时 AI 功能给出明确提示）
    ai_base_url: str = ""
    ai_key: str = ""
    ai_model: str = ""

    def __post_init__(self) -> None:
        if self.relay_groups is None:
            self.relay_groups = ["全员组"]


class NodeAgent:
    """本地核心组装者：挂载/排除管理、索引、检索、监视。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = IndexDB(config.db_path)
        from ai.ask_sessions import AskSessionStore
        from ai.sessions import AISessionStore

        self.ai_sessions = AISessionStore(config.db_path)
        self.ask_sessions = AskSessionStore(config.db_path)
        self.engine = ExclusionEngine(max_file_size=config.max_file_size)
        self.indexer = Indexer(
            self.db,
            chunker=Chunker(
                min_chars=config.chunk_min,
                max_chars=config.chunk_max,
                overlap=config.chunk_overlap,
                max_chunks=config.max_chunks,
            ),
            trigram_enabled=config.trigram_enabled,
            trigram_max_chars=config.trigram_max_chars,
        )
        self.searcher = Searcher(self.db)
        # M7 向量混合检索（默认关；开启后索引与检索自动带向量）
        from ai.embedding import VectorConfigStore

        self.vector_store_cfg = VectorConfigStore(config.config_dir)
        self.vector_config = self.vector_store_cfg.load()
        self.vector_index = None
        self._build_vector_index()
        self.remote_searcher = Searcher(self.db, scope="remote", vector_index=self.vector_index)
        # P1 重排（默认关；提问端精排，失败回退 P0）
        from ai.rerank import RerankConfigStore, build_reranker

        self.rerank_store = RerankConfigStore(config.config_dir)
        self.rerank_config = self.rerank_store.load()
        self.reranker = build_reranker(self.rerank_config)
        self._rerank_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._rerank_cache_lock = threading.Lock()
        self.service = WatchService(
            self.indexer,
            self.db,
            debounce=config.debounce,
            cpu_threshold=config.cpu_threshold,
        )
        self._connectors: dict[int, Connector] = {}
        from ai.llm import AIConfigStore

        self.ai_store = AIConfigStore(config.config_dir)
        self.ai_config = self.ai_store.load()
        if config.ai_base_url or config.ai_model:
            self.ai_config.enabled = True
            self.ai_config.base_url = config.ai_base_url or self.ai_config.base_url
            self.ai_config.model = config.ai_model or self.ai_config.model
            self.ai_config.api_key = config.ai_key or self.ai_config.api_key
        # P2 查询改写（默认关；仅提问端召回使用）
        from ai.query_rewrite import QueryExpandStore, QueryRewriter

        self.query_store = QueryExpandStore(config.config_dir)
        self.query_config = self.query_store.load()
        self.rewriter = QueryRewriter(self.query_config, self.ai_config)
        # M6：relay 配置持久化（设置页保存）——CLI 参数优先（便于测试/运维）
        self.relay_store_path = config.config_dir / "relay.json"
        if not (config.relay_url or config.relay_enabled) and self.relay_store_path.exists():
            try:
                stored = json.loads(self.relay_store_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.exception("读取 relay.json 失败")
                stored = {}
            if isinstance(stored, dict):
                config.relay_enabled = bool(stored.get("enabled", True))
                config.relay_url = str(stored.get("relay_url") or "")
                config.relay_nick = str(stored.get("nick") or config.relay_nick)
                config.relay_groups = list(stored.get("groups") or ["全员组"])
                config.relay_token = str(stored.get("token") or "")
        from node.bus import NodeBus, RelayConfig

        self.bus = NodeBus(
            RelayConfig(
                enabled=config.relay_enabled,
                relay_url=config.relay_url,
                nick=config.relay_nick,
                groups=list(config.relay_groups or ["全员组"]),
                relay_token=config.relay_token,
                ask_deadline_ms=config.ask_deadline_ms,
                answer_budget_ms=config.answer_budget_ms,
            ),
            self.remote_searcher,
            search_fn=self.search_local_expanded,
            rewrite_fn=lambda q: self.rewriter.rewrite(q),
            plan_fn=lambda q: self.rewriter.plan(q),
            local_searcher=self.searcher,
        )
        self._load_state()

    # ---- 状态加载 ----
    def _load_state(self) -> None:
        self._reload_rules()
        for mount in self.db.list_mounts():
            if int(getattr(mount, "enabled", 1)) == 1:
                self._attach_mount(mount)

    def _build_connector(self, kind: str, path: Path) -> Connector:
        if kind == "obsidian":
            from connectors.obsidian import ObsidianConnector

            return ObsidianConnector(
                path, text_cap_chars=self.config.text_cap_chars, engine=self.engine
            )
        if kind == "siyuan":
            from connectors.siyuan import SiyuanConnector

            return SiyuanConnector(
                path, text_cap_chars=self.config.text_cap_chars, engine=self.engine
            )
        return DirectoryConnector(
            path, text_cap_chars=self.config.text_cap_chars, engine=self.engine
        )

    def _attach_mount(self, mount: MountRecord) -> None:
        conn = self._build_connector(mount.kind, Path(mount.path))
        self._connectors[mount.mount_id] = conn
        self.service.add_mount(mount.mount_id, conn)
        # 启动全量校验一次（设计 §5）
        self.service.enqueue_scan(mount.mount_id, conn)

    def connector_of(self, mount_id: int) -> Optional[Connector]:
        return self._connectors.get(mount_id)

    # ---- 挂载管理 ----
    @staticmethod
    def _find_siyuan_workspace(p: Path) -> Optional[Path]:
        """沿祖先目录查找合法思源工作区（conf/conf.json 可解析 + data/ 目录）。"""
        cur = p if p.is_dir() else p.parent
        for cand in [cur, *cur.parents]:
            conf = cand / "conf" / "conf.json"
            if not (conf.is_file() and (cand / "data").is_dir()):
                continue
            try:
                if isinstance(json.loads(conf.read_text(encoding="utf-8")), dict):
                    return cand
            except (OSError, ValueError):
                continue
        return None

    def _detect_mount_kind(self, p: Path) -> str:
        """自动识别挂载类型（M3 用户反馈：误挂思源笔记本目录导致 .sy 不被识别）。"""
        if p.is_file():
            return "file"
        ws = self._find_siyuan_workspace(p)
        if ws is not None:
            if ws == p:
                return "siyuan"
            raise ValueError(
                "「{}」位于思源工作区内（工作区根目录：{}）。思源笔记请挂载工作区根目录"
                "（类型选「思源工作区」）；若确要按普通目录挂载，请在类型中显式选择「目录」。".format(p, ws)
            )
        if (p / ".obsidian").is_dir():
            return "obsidian"
        return "directory"

    def add_mount(self, path: str, kind: Optional[str] = None) -> dict:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise ValueError("路径不存在: " + str(p))
        if kind in (None, "", "auto"):
            kind = self._detect_mount_kind(p)
        if kind not in ("directory", "obsidian", "file", "siyuan"):
            raise ValueError("未知挂载类型: " + kind)
        # 红线②：索引库绝不放挂载目录内——拒绝挂载 DB 文件本身，
        # 或任何「包含 DB」的目录（DB 自身或其祖先目录作为挂载点）
        db_path = self.config.db_path.resolve()
        if p == db_path or (p.is_dir() and (db_path == p or p in db_path.parents)):
            raise ValueError("禁止挂载索引库所在目录（红线②）")
        if self.db.mount_by_path(str(p)) is not None:
            raise ValueError("已挂载过: " + str(p))
        mount_id = self.db.add_mount(kind, str(p))
        mount = self.db.get_mount(mount_id)
        if mount is None:
            raise ValueError("挂载创建失败")
        self._attach_mount(mount)
        return {"mount_id": mount_id, "kind": kind, "path": str(p)}

    def remove_mount(self, mount_id: int) -> None:
        self.service.remove_mount(mount_id)
        self._connectors.pop(mount_id, None)
        self.db.remove_mount(mount_id)

    def set_mount_enabled(self, mount_id: int, enabled: bool) -> dict:
        mount = self.db.get_mount(mount_id)
        if mount is None:
            raise LookupError("挂载不存在")
        self.db.set_mount_enabled(mount_id, enabled)
        if enabled:
            if mount_id not in self._connectors:
                self._attach_mount(self.db.get_mount(mount_id))
            else:
                self.service.enqueue_scan(mount_id, self._connectors[mount_id])
        else:
            self.service.remove_mount(mount_id)
            self._connectors.pop(mount_id, None)
        return {"ok": True, "mount_id": mount_id, "enabled": bool(enabled)}

    def set_doc_share(self, doc_id: int, state: str) -> dict:
        doc = self.db.get_doc_by_id(doc_id)
        if doc is None:
            raise LookupError("文档不存在")
        self.db.set_doc_share(doc_id, state)
        return {"ok": True, "doc": {"doc_id": doc.doc_id, "share_state": state}}

    # ---- 排除规则 ----
    def _reload_rules(self) -> None:
        self.engine.set_rules(
            [ExcludeRule(k, p) for (_i, k, p) in self.db.list_exclude_rules()]
        )

    def add_exclude_rule(self, kind: str, pattern: str) -> dict:
        if kind not in ("file", "dir", "glob"):
            raise ValueError("规则类型须为 file/dir/glob")
        if kind in ("file", "dir"):
            pattern = str(Path(pattern).expanduser().resolve())
        rule_id = self.db.add_exclude_rule(kind, pattern)
        self._reload_rules()
        self._apply_new_rule(ExcludeRule(kind, pattern))
        return {"rule_id": rule_id, "kind": kind, "pattern": pattern}

    def remove_exclude_rule(self, rule_id: int) -> None:
        self.db.remove_exclude_rule(rule_id)
        self._reload_rules()
        self.reindex_all()  # 规则放宽后重扫恢复（签名校验跳过未变文档，代价低）

    def _apply_new_rule(self, rule: ExcludeRule) -> None:
        """新增规则立即生效（红线⑤）：命中新规则的已索引文档 -> 摘除 chunk、状态 excluded。"""
        for doc in self.db.list_docs(status="indexed", limit=None):
            abs_path = Path(doc["abs_path"])
            if rule.matches(abs_path, doc["rel_path"]):
                self.db.upsert_doc(
                    doc["mount_id"],
                    doc["rel_path"],
                    doc["abs_path"],
                    doc["title"],
                    doc["format"],
                    doc["mtime"],
                    doc["size"],
                    doc["hash"],
                    status="excluded",
                    error="用户排除规则({}): {}".format(rule.kind, rule.pattern),
                )
                self.db.clear_chunks(int(doc["doc_id"]))

    def reindex_all(self) -> None:
        for mount_id, conn in self._connectors.items():
            self.service.enqueue_scan(mount_id, conn)

    # ---- 笔记分组（设计 §15.6；M3-2，GR-07 deny-union 裁决） ----
    def add_group(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("组名不能为空")
        group_id = self.db.add_group(name)
        return {"group_id": group_id, "name": name}

    def remove_group(self, group_id: int) -> None:
        """删除组：仅删除分组关系，已生成的排除规则不受影响（GR-07）。"""
        if self.db.get_group(group_id) is None:
            raise LookupError("分组不存在")
        self.db.remove_group(group_id)

    def add_group_member(self, group_id: int, mount_id: int, rel_path: str = "") -> dict:
        if self.db.get_group(group_id) is None:
            raise LookupError("分组不存在")
        if self.db.get_mount(mount_id) is None:
            raise LookupError("挂载不存在")
        member_id = self.db.add_group_member(group_id, mount_id, rel_path)
        return {"member_id": member_id, "group_id": group_id, "mount_id": mount_id, "rel_path": rel_path}

    def remove_group_member(self, member_id: int) -> None:
        self.db.remove_group_member(member_id)

    def exclude_group(self, group_id: int) -> dict:
        """整组批量排除（GR-07 裁决）：为每个成员生成真实排除规则记录。

        - 整挂载成员（rel_path=''）：目录挂载 -> dir 规则；单文件挂载 -> file 规则；
        - 具体文档成员：file 规则（绝对路径）。
        规则写入 exclude_rules 表（API/DB 可查）、立即生效（_apply_new_rule）；
        删除组不影响规则；规则可单独删除（删除后文件恢复可见）。
        返回新增规则列表。
        """
        if self.db.get_group(group_id) is None:
            raise LookupError("分组不存在")
        created: list[dict] = []
        existing = {(k, p) for (_i, k, p) in self.db.list_exclude_rules()}
        for member in self.db.list_group_members(group_id):
            mount = self.db.get_mount(member["mount_id"])
            if mount is None:
                continue
            conn = self.connector_of(member["mount_id"])
            if member["rel_path"]:
                # 具体文档：file 规则
                try:
                    abs_path = str(conn.resolve(member["rel_path"])) if conn else str(Path(mount.path) / member["rel_path"])
                except Exception:
                    continue
                kind, pattern = "file", abs_path
            elif mount.kind == "file":
                kind, pattern = "file", str(Path(mount.path))
            else:
                kind, pattern = "dir", str(Path(mount.path))
            if (kind, pattern) in existing:
                continue  # 已有同规则：跳过（幂等）
            rule_id = self.db.add_exclude_rule(kind, pattern)
            existing.add((kind, pattern))
            created.append({"rule_id": rule_id, "kind": kind, "pattern": pattern})
        self._reload_rules()
        for rule in created:
            self._apply_new_rule(ExcludeRule(rule["kind"], rule["pattern"]))
        return {"group_id": group_id, "rules_created": created}

    # ---- 生命周期 ----
    def start(self) -> None:
        self.service.start_worker()

    # ---- Relay 配置（M6） ----
    def _relay_config_obj(self):
        from node.bus import RelayConfig

        c = self.config
        return RelayConfig(
            enabled=bool(c.relay_enabled),
            relay_url=c.relay_url,
            nick=c.relay_nick or "我",
            groups=list(c.relay_groups or ["全员组"]),
            relay_token=c.relay_token,
            ask_deadline_ms=c.ask_deadline_ms,
            answer_budget_ms=c.answer_budget_ms,
        )

    def relay_public(self) -> dict:
        c = self.config
        tok = c.relay_token or ""
        return {
            "enabled": bool(c.relay_enabled),
            "relay_url": c.relay_url,
            "nick": c.relay_nick,
            "groups": list(c.relay_groups or []),
            "has_token": bool(tok),
            "masked_token": ("****" + tok[-4:]) if len(tok) > 4 else "",
            "connected": self.bus.connected,
            "online": self.bus.online,
            "server_groups": self.bus.client.known_groups,
            "known_groups": self.bus.client.known_groups,
            "last_error": self.bus.client.last_error,
            "dependency_ok": self.bus.client.dependency_ok,
        }

    def save_relay(self, data: dict) -> dict:
        c = self.config
        if "relay_url" in data:
            c.relay_url = str(data.get("relay_url") or "").strip()
        if "nick" in data:
            c.relay_nick = str(data.get("nick") or "").strip() or c.relay_nick
        if data.get("token"):
            c.relay_token = str(data.get("token") or "").strip()
        if "groups" in data:
            c.relay_groups = [
                str(g).strip() for g in (data.get("groups") or []) if str(g).strip()
            ] or ["全员组"]
        if "enabled" in data:
            c.relay_enabled = bool(data.get("enabled"))
        elif c.relay_url:
            c.relay_enabled = True
        if not c.relay_url:
            c.relay_enabled = False
        payload = {
            "enabled": bool(c.relay_enabled),
            "relay_url": c.relay_url,
            "nick": c.relay_nick,
            "token": c.relay_token,
            "groups": list(c.relay_groups or []),
        }
        self.relay_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.relay_store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.relay_public()

    async def restart_bus(self) -> None:
        """按最新配置重建 relay 连接（保存设置后调用）。"""
        from node.bus import NodeBus

        await self.bus.stop()
        self.bus = NodeBus(
            self._relay_config_obj(), self.remote_searcher,
            search_fn=self.search_local_expanded,
            rewrite_fn=lambda q: self.rewriter.rewrite(q),
            plan_fn=lambda q: self.rewriter.plan(q),
            local_searcher=self.searcher,
        )
        self.bus.start()

    async def create_group(self, name: str, description: str = "") -> dict:
        return await self.bus.create_group(name, description)

    async def test_relay(self, data: dict) -> dict:
        """设置页连通性测试；overrides 只用于本次测试，不落盘。"""
        from node.bus import probe_relay

        c = self.config
        url = str(data.get("relay_url") or c.relay_url or "").strip()
        nick = str(data.get("nick") or c.relay_nick or "").strip()
        token = str(data.get("token") or c.relay_token or "").strip()
        groups = data.get("groups") or list(c.relay_groups or ["全员组"])
        return await probe_relay(url, nick, token, list(groups))

    async def start_bus(self) -> None:
        """在事件循环内启动 relay 长连接（FastAPI startup 调用；未配置时无操作）。"""
        self.bus.start()

    async def stop_bus(self) -> None:
        await self.bus.stop()

    def stop(self) -> None:
        self.service.stop_worker()
        self.service.shutdown()
        try:
            self.ai_sessions.close()
        except Exception:
            pass
        try:
            self.ask_sessions.close()
        except Exception:
            pass
        self.db.close()

    # ---- AI（M5） ----
    def ai_public(self) -> dict:
        return self.ai_config.public_dict()

    def update_ai(self, data: dict) -> dict:
        """更新并持久化 AI 配置（api_key 为空表示保持不变）。"""
        cfg = self.ai_config
        from ai.llm import merge_headers, normalize_base_url

        if "mode" in data and data.get("mode") is not None:
            cfg.mode = "gateway" if str(data.get("mode")).lower() == "gateway" else "openai"
        if "base_url" in data:
            raw = str(data.get("base_url") or "").strip()
            cfg.base_url = raw if cfg.mode == "gateway" else normalize_base_url(raw)
        if "placeholder_files" in data:
            raw_files = data.get("placeholder_files") or {}
            cfg.placeholder_files = {
                str(k).strip(): str(v).strip()
                for k, v in (raw_files.items() if isinstance(raw_files, dict) else [])
                if str(k).strip() and str(v).strip()
            }
        if "headers" in data:
            cfg.headers = merge_headers(cfg.headers, data.get("headers") or [])
        if "model" in data:
            cfg.model = str(data.get("model") or "").strip()
        if "api_key" in data and data.get("api_key"):
            cfg.api_key = str(data.get("api_key")).strip()
        if "temperature" in data and data.get("temperature") is not None:
            cfg.temperature = float(data.get("temperature"))
        if "enabled" in data:
            cfg.enabled = bool(data.get("enabled"))
        elif data.get("base_url") or data.get("model"):
            cfg.enabled = True  # 保存地址/模型即视为启用（未显式关闭）
        if "use_system_proxy" in data and data.get("use_system_proxy") is not None:
            cfg.use_system_proxy = bool(data.get("use_system_proxy"))
        if "system_prompt" in data and data.get("system_prompt") is not None:
            cfg.system_prompt = str(data.get("system_prompt")).strip()
        if "max_context_chars" in data and data.get("max_context_chars") is not None:
            cfg.max_context_chars = max(4000, min(100000, int(data.get("max_context_chars"))))
        if "top_n" in data and data.get("top_n") is not None:
            cfg.top_n = max(4, min(60, int(data.get("top_n"))))
        if "max_tokens" in data and data.get("max_tokens") is not None:
            cfg.max_tokens = max(500, min(16000, int(data.get("max_tokens"))))
        cfg.enabled = bool(cfg.enabled and cfg.base_url and cfg.model)
        self.ai_store.save(cfg)
        return cfg.public_dict()

    def ai_ping(self, overrides: Optional[dict] = None) -> dict:
        """连通性测试。overrides 为表单里尚未保存的值（base_url/model/api_key/use_system_proxy）。"""
        from dataclasses import replace

        from ai.llm import LLMClient, merge_headers, normalize_base_url

        cfg = replace(self.ai_config)
        data = overrides or {}
        if data.get("mode") is not None:
            cfg.mode = "gateway" if str(data.get("mode")).lower() == "gateway" else "openai"
        if data.get("base_url") is not None:
            raw = str(data.get("base_url") or "").strip()
            cfg.base_url = raw if cfg.mode == "gateway" else normalize_base_url(raw)
        if data.get("placeholder_files") is not None:
            raw_files = data.get("placeholder_files") or {}
            cfg.placeholder_files = {
                str(k).strip(): str(v).strip()
                for k, v in (raw_files.items() if isinstance(raw_files, dict) else [])
                if str(k).strip() and str(v).strip()
            }
        if data.get("headers") is not None:
            cfg.headers = merge_headers(cfg.headers, data.get("headers") or [])
        if data.get("model"):
            cfg.model = str(data["model"]).strip()
        if data.get("api_key"):
            cfg.api_key = str(data["api_key"]).strip()
        if data.get("use_system_proxy") is not None:
            cfg.use_system_proxy = bool(data["use_system_proxy"])
        if not (cfg.base_url and cfg.model):
            raise ValueError("请先填写 base_url 与 model")
        cfg.enabled = True
        return LLMClient(cfg).ping()

    def enrich_local_context(self, aggregated: list[dict]) -> list[dict]:
        """本地命中用真实分块文本（含相邻块）替换短片段，避免 AI 只看到 150 字。"""
        from ai.rag import select_local_window

        nick = self.config.relay_nick or "我"
        out: list[dict] = []
        for hit in aggregated:
            h = dict(hit)
            local = next(
                (s for s in (h.get("sources") or []) if s and s.get("node") == nick),
                None,
            )
            doc_id = (local or {}).get("doc_id")
            if doc_id:
                try:
                    chunks = self.db.get_chunk_texts(int(doc_id))
                except Exception:
                    chunks = []
                if chunks:
                    full = "\n\n".join(chunks)
                    full_doc = len(full) <= 12000
                    if full_doc:
                        text = full  # 中小笔记给全文（含代码块）
                    else:
                        # 长文档：开头目录/背景 + 命中块上下文 + 结尾，尽量不把有效信息截掉
                        budget = max(5000, min(14000, int(getattr(self.ai_config, "max_context_chars", 24000) * 0.55)))
                        head = full[:1800]
                        window = select_local_window(
                            chunks, int(h.get("chunk_seq") or 0),
                            max_chars=max(3000, budget - len(head) - 400),
                        )
                        tail = full[-700:] if len(full) > 700 else ""
                        parts = [head]
                        if window and window not in head:
                            parts.append("……（中间省略）……")
                            parts.append(window)
                        if tail and tail not in "\n\n".join(parts):
                            parts.append("……（结尾）……")
                            parts.append(tail)
                        text = "\n\n".join(parts)[:budget]
                    if text:
                        h["snippet_text"] = text
                        h["context_full"] = True
                        h["context_full_doc"] = full_doc
                        h["context_chars"] = len(text)
            out.append(h)
        return out

    def enrich_display(self, aggregated: list[dict]) -> list[dict]:
        """展示用：本地命中换成更长的分块窗口（Markdown 渲染更有内容）。"""
        from ai.rag import select_local_window

        nick = self.config.relay_nick or "我"
        out: list[dict] = []
        for hit in aggregated:
            h = dict(hit)
            local = next(
                (s for s in (h.get("sources") or []) if s and s.get("node") == nick),
                None,
            )
            doc_id = (local or {}).get("doc_id")
            if doc_id:
                try:
                    chunks = self.db.get_chunk_texts(int(doc_id))
                except Exception:
                    chunks = []
                if chunks:
                    text = select_local_window(chunks, int(h.get("chunk_seq") or 0), max_chars=1200)
                    if text:
                        h["snippet_text"] = text
            out.append(h)
        return out

    def ai_answer(self, question: str, aggregated: list[dict], history: Optional[list] = None) -> dict:
        from ai.rag import answer_question

        strong = [h for h in aggregated if str(h.get("tier") or "strong") != "weak"]
        context_hits = strong if strong else []
        return answer_question(
            question, self.enrich_local_context(context_hits), self.ai_config, history=history
        )

    # ---- M8 随手问会话 ----
    def ask_session_list(self) -> list[dict]:
        return self.ask_sessions.list_sessions()

    def ask_session_create(self, title: str = "") -> dict:
        return self.ask_sessions.create(title)

    def ask_session_get(self, sid: str) -> dict:
        session = self.ask_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        return {"ok": True, "session": session, "turns": self.ask_sessions.turns(sid)}

    def ask_session_rename(self, sid: str, title: str) -> dict:
        ok = self.ask_sessions.rename(sid, title)
        return {"ok": ok, "session": self.ask_sessions.get(sid)}

    def ask_session_delete(self, sid: str) -> dict:
        return {"ok": self.ask_sessions.delete(sid)}

    def _pair_history(self, turns: list[dict]) -> list[dict]:
        pairs: list[dict] = []
        pending = ""
        for turn in turns:
            if turn.get("role") == "user":
                pending = str(turn.get("content") or "")
            elif turn.get("role") == "assistant" and pending:
                pairs.append({"question": pending, "answer": str(turn.get("content") or "")})
                pending = ""
        return pairs

    async def ask_session_ask(self, sid: str, text: str, groups: Optional[list] = None) -> dict:
        session = self.ask_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        question = (text or "").strip()
        if not question:
            return {"ok": False, "error": "问题不能为空"}
        result = await self.bus.ask(question, groups)
        qid = str(result.get("qid") or "")
        turn = self.ask_sessions.append(
            sid, "user", question,
            {"qid": qid, "groups": result.get("groups") or [], "aggregated": [], "plan": result.get("plan")},
        )
        if session.get("title") == "新会话":
            self.ask_sessions.rename(sid, question[:20])
        return {"ok": True, "session_id": sid, "qid": qid, "turn_id": turn["id"], "session": self.ask_sessions.get(sid)}

    def ask_session_complete(self, sid: str, turn_id: int, qid: str) -> dict:
        session = self.ask_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        view = self.bus.view(qid) or {}
        aggregated = view.get("aggregated") or []
        if aggregated:
            aggregated = self.enrich_display(aggregated)
            aggregated = self.rerank_hits(str(view.get("text") or ""), aggregated)
        turns = self.ask_sessions.turns(sid)
        payload = {
            "qid": qid,
            "aggregated": aggregated[:30],
            "search_text": view.get("search_text") or question,
            "plan": view.get("plan"),
            "answers": view.get("answers") or [],
            "elapsed_ms": view.get("elapsed_ms") or 0,
            "expected": view.get("expected") or 0,
            "responded": view.get("responded") or 0,
            "status": view.get("status") or "",
        }
        self.ask_sessions.update_payload(int(turn_id), payload)
        return {"ok": True, "aggregated": aggregated, "session": self.ask_sessions.get(sid),
                "turns": self.ask_sessions.turns(sid)}

    def ask_session_summarize(self, sid: str) -> dict:
        session = self.ask_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        turns = self.ask_sessions.turns(sid)
        target = None
        for turn in reversed(turns):
            if turn.get("role") == "user":
                target = turn
                break
        if target is None:
            return {"ok": False, "error": "本会话还没有可综合的搜索结果"}
        aggregated = (target.get("payload") or {}).get("aggregated") or []
        if not aggregated:
            return {"ok": False, "needs_search": True,
                    "error": "最近一轮没有检索结果；请重新检索或重新提问"}
        history = self._pair_history([t for t in turns if int(t["id"]) < int(target["id"])])
        answer = self.ai_answer(str(target.get("content") or ""), aggregated, history=history)
        if answer.get("answer"):
            self.ask_sessions.append(sid, "assistant", str(answer.get("answer")),
                                   {"citations": answer.get("citations") or [], "status": answer.get("status"),
                                    "elapsed_ms": answer.get("elapsed_ms") or 0, "model": answer.get("model") or "",
                                    "context_chars": answer.get("context_chars") or 0})
        return {"ok": answer.get("status") == "ready", "answer": answer, "session": self.ask_sessions.get(sid),
                "turns": self.ask_sessions.turns(sid)}

    def ask_session_followup(self, sid: str, text: str, summarize: bool = True) -> dict:
        session = self.ask_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        question = (text or "").strip()
        if not question:
            return {"ok": False, "error": "追问不能为空"}
        turns = self.ask_sessions.turns(sid)
        aggregated: list = []
        latest_user = None
        for turn in reversed(turns):
            if turn.get("role") == "user":
                latest_user = turn
                break
        if latest_user is not None:
            payload = latest_user.get("payload") or {}
            aggregated = payload.get("aggregated") or []
            if not aggregated and payload.get("qid"):
                view = self.bus.view(str(payload["qid"])) or {}
                aggregated = view.get("aggregated") or []
        if not aggregated:
            return {"ok": False, "needs_search": True,
                    "error": "最近一轮没有检索结果；请改用「重新检索」或重新提问"}
        self.ask_sessions.append(sid, "user", question, {"aggregated": aggregated[:30]})
        if not summarize:
            return {"ok": True, "answer": None, "aggregated": aggregated, "session": self.ask_sessions.get(sid),
                    "turns": self.ask_sessions.turns(sid)}
        history = self._pair_history(turns)
        answer = self.ai_answer(question, aggregated, history=history)
        if answer.get("answer"):
            self.ask_sessions.append(sid, "assistant", str(answer.get("answer")),
                                   {"citations": answer.get("citations") or [], "status": answer.get("status"),
                                    "elapsed_ms": answer.get("elapsed_ms") or 0, "model": answer.get("model") or "",
                                    "context_chars": answer.get("context_chars") or 0})
        return {"ok": True, "answer": answer, "aggregated": aggregated, "session": self.ask_sessions.get(sid),
                "turns": self.ask_sessions.turns(sid)}

    def ask_session_summarize_stream(self, sid: str):
        """流式综合当前会话最新搜索结果。"""
        from ai.rag import stream_answer

        session = self.ask_sessions.get(sid)
        if session is None:
            yield {"type": "error", "error": "会话不存在"}
            return
        turns = self.ask_sessions.turns(sid)
        target = None
        for turn in reversed(turns):
            if turn.get("role") == "user":
                target = turn
                break
        if target is None:
            yield {"type": "error", "error": "本会话还没有可综合的搜索结果"}
            return
        aggregated = (target.get("payload") or {}).get("aggregated") or []
        if not aggregated:
            yield {"type": "error", "needs_search": True,
                   "error": "最近一轮没有检索结果；请重新检索或重新提问"}
            return
        aggregated = self.enrich_local_context(aggregated)
        history = self._pair_history([t for t in turns if int(t["id"]) < int(target["id"])])
        for ev in stream_answer(str(target.get("content") or ""), aggregated, self.ai_config, history=history):
            if ev.get("type") == "done":
                text = str(ev.get("text") or "")
                if text:
                    self.ask_sessions.append(sid, "assistant", text, {
                        "citations": ev.get("citations") or [],
                        "status": "ready",
                        "elapsed_ms": ev.get("elapsed_ms") or 0,
                        "model": ev.get("model") or "",
                        "context_chars": ev.get("context_chars") or 0,
                    })
                ev["turns"] = self.ask_sessions.turns(sid)
                ev["session"] = self.ask_sessions.get(sid)
            yield ev

    def ask_session_followup_stream(self, sid: str, text: str, summarize: bool = True):
        """流式追问；summarize=False 时只记录追问。"""
        from ai.rag import stream_answer

        session = self.ask_sessions.get(sid)
        if session is None:
            yield {"type": "error", "error": "会话不存在"}
            return
        question = (text or "").strip()
        if not question:
            yield {"type": "error", "error": "追问不能为空"}
            return
        turns = self.ask_sessions.turns(sid)
        aggregated: list = []
        latest_user = None
        for turn in reversed(turns):
            if turn.get("role") == "user":
                latest_user = turn
                break
        if latest_user is not None:
            payload = latest_user.get("payload") or {}
            aggregated = payload.get("aggregated") or []
            if not aggregated and payload.get("qid"):
                view = self.bus.view(str(payload["qid"])) or {}
                aggregated = view.get("aggregated") or []
        if not aggregated:
            yield {"type": "error", "needs_search": True,
                   "error": "最近一轮没有检索结果；请改用「重新检索」或重新提问"}
            return
        history = self._pair_history(turns)
        aggregated = self.enrich_local_context(aggregated)
        user_turn = self.ask_sessions.append(sid, "user", question, {"aggregated": aggregated[:30]})
        if not summarize:
            yield {"type": "done", "text": "", "user_turn": user_turn, "answer": None,
                   "turns": self.ask_sessions.turns(sid), "session": self.ask_sessions.get(sid)}
            return
        for ev in stream_answer(question, aggregated, self.ai_config, history=history):
            if ev.get("type") == "start":
                ev["user_turn"] = user_turn
            if ev.get("type") == "done":
                answer = str(ev.get("text") or "")
                if answer:
                    self.ask_sessions.append(sid, "assistant", answer, {
                        "citations": ev.get("citations") or [],
                        "status": "ready",
                        "elapsed_ms": ev.get("elapsed_ms") or 0,
                        "model": ev.get("model") or "",
                        "context_chars": ev.get("context_chars") or 0,
                    })
                ev["turns"] = self.ask_sessions.turns(sid)
                ev["session"] = self.ask_sessions.get(sid)
            yield ev

    # ---- M8 AI 会话 ----
    def ai_session_list(self) -> list[dict]:
        return self.ai_sessions.list_sessions()

    def ai_session_create(self, title: str = "") -> dict:
        return self.ai_sessions.create(title)

    def ai_session_rename(self, sid: str, title: str) -> dict:
        ok = self.ai_sessions.rename(sid, title)
        return {"ok": ok, "session": self.ai_sessions.get(sid)}

    def ai_session_delete(self, sid: str) -> dict:
        return {"ok": self.ai_sessions.delete(sid)}

    def ai_session_get(self, sid: str) -> dict:
        session = self.ai_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        return {"ok": True, "session": session, "messages": self.ai_sessions.messages(sid)}

    def _history_pairs(self, messages: list[dict]) -> list[dict]:
        pairs: list[dict] = []
        pending = ""
        for msg in messages:
            if msg.get("role") == "user":
                pending = str(msg.get("content") or "")
            elif msg.get("role") == "assistant" and pending:
                pairs.append({"question": pending, "answer": str(msg.get("content") or "")})
                pending = ""
        return pairs

    def ai_session_ask(self, sid: str, text: str) -> dict:
        session = self.ai_sessions.get(sid)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        question = (text or "").strip()
        if not question:
            return {"ok": False, "error": "问题不能为空"}
        history = self._history_pairs(self.ai_sessions.messages(sid))
        if session.get("title") == "新会话" and not history:
            self.ai_sessions.rename(sid, question[:20])
            session = self.ai_sessions.get(sid) or session
        try:
            hits = self.search(question, 8)
        except Exception:
            log.exception("AI 会话检索失败")
            hits = []
        self.ai_sessions.append(sid, "user", question)
        answer = self.ai_answer(question, hits, history=history)
        if answer.get("answer"):
            self.ai_sessions.append(sid, "assistant", str(answer.get("answer")))
        return {
            "ok": answer.get("status") == "ready",
            "answer": answer,
            "hits": hits,
            "session": self.ai_sessions.get(sid),
            "messages": self.ai_sessions.messages(sid),
        }

    def ask_followup(self, qid: str, text: str) -> dict:
        bus = getattr(self, "bus", None)
        item = bus.asks.items.get(qid) if bus is not None else None
        if item is None:
            return {"ok": False, "error": "提问不存在或已过期"}
        question = (text or "").strip()
        if not question:
            return {"ok": False, "error": "追问不能为空"}
        view = bus.view(qid) or {}
        aggregated = view.get("aggregated") or []
        history: list[dict] = []
        if item.text:
            history.append({"question": item.text, "answer": str((item.ai or {}).get("answer") or "")})
        for turn in item.followups:
            history.append({"question": str(turn.get("text") or ""), "answer": str(turn.get("answer") or "")})
        answer = self.ai_answer(question, aggregated, history=history)
        item.followups.append({"text": question, "answer": answer, "created_at": time.time()})
        return {"ok": answer.get("status") == "ready", "answer": answer, "followups": item.followups}

    # ---- 向量检索（M7） ----
    def _build_vector_index(self) -> None:
        """按配置构建向量索引；失败回退关键词检索（不影响主流程）。"""
        from ai.embedding import LocalOnnxEmbeddings, OpenAIEmbeddings
        from ai.vector_backend import build_backend
        from ai.vector_index import VectorIndex
        from ai.vectorstore import VectorStore

        cfg = self.vector_config
        self.vector_index = None
        self.searcher.vector_index = None
        self.indexer.on_indexed = None
        if not cfg.enabled:
            return
        try:
            if cfg.provider == "openai":
                embeddings = OpenAIEmbeddings(
                    cfg.base_url, cfg.model, cfg.api_key,
                    dim=cfg.dim, use_system_proxy=cfg.use_system_proxy,
                )
            else:
                embeddings = LocalOnnxEmbeddings(cfg.model_dir or None, dim=cfg.dim)
            store = VectorStore(self.config.db_path, dim=cfg.dim)
            backend = build_backend(cfg.backend, self.config.db_path, cfg.dim, store=store)
            self.vector_index = VectorIndex(
                self.db, store, embeddings, backend=backend,
                min_similarity=cfg.min_similarity,
                vector_only_max=cfg.vector_only_max,
                require_lexical=cfg.require_lexical,
            )
            self.searcher.vector_index = self.vector_index
            if hasattr(self, "remote_searcher"):
                self.remote_searcher.vector_index = self.vector_index
            if self.vector_index.ready:
                self.indexer.on_indexed = self.vector_index.index_doc
        except Exception:
            log.exception("初始化向量索引失败，回退关键词检索")
            self.vector_index = None
            self.searcher.vector_index = None

    def vector_public(self) -> dict:
        cfg = self.vector_config
        data = cfg.public_dict()
        vi = self.vector_index
        data.update({
            "ready": bool(vi is not None and vi.ready),
            "vectors": (vi.backend.count() if vi is not None else 0),
            "error": (vi.last_error if vi is not None else ""),
            "backend": (vi.backend.name if vi is not None else cfg.backend),
            "metric": (vi.backend.metric if vi is not None else ""),
            "model_id": (vi.model_id if vi is not None else ""),
            "stale": bool(vi.stale) if vi is not None else False,
            "meta": (vi.backend.get_meta() if vi is not None else {}),
        })
        return data

    def save_vector(self, data: dict) -> dict:
        cfg = self.vector_config
        if "enabled" in data:
            cfg.enabled = bool(data.get("enabled"))
        if "provider" in data:
            cfg.provider = str(data.get("provider") or "local")
        if "model_dir" in data and data.get("model_dir") is not None:
            cfg.model_dir = str(data.get("model_dir") or "").strip()
        if "base_url" in data and data.get("base_url") is not None:
            cfg.base_url = str(data.get("base_url") or "").strip()
        if "model" in data and data.get("model") is not None:
            cfg.model = str(data.get("model") or "").strip()
        if data.get("api_key"):
            cfg.api_key = str(data.get("api_key") or "").strip()
        if "dim" in data and data.get("dim"):
            cfg.dim = int(data.get("dim"))
        if "top_k" in data and data.get("top_k"):
            cfg.top_k = int(data.get("top_k"))
        if "use_system_proxy" in data and data.get("use_system_proxy") is not None:
            cfg.use_system_proxy = bool(data.get("use_system_proxy"))
        if "min_similarity" in data and data.get("min_similarity") is not None:
            cfg.min_similarity = float(data.get("min_similarity"))
        if "vector_only_max" in data and data.get("vector_only_max") is not None:
            cfg.vector_only_max = int(data.get("vector_only_max"))
        if "require_lexical" in data and data.get("require_lexical") is not None:
            cfg.require_lexical = bool(data.get("require_lexical"))
        if "backend" in data and data.get("backend"):
            cfg.backend = str(data.get("backend") or "sqlite-vec")
        if cfg.enabled and cfg.provider == "openai" and not (cfg.base_url and cfg.model):
            cfg.enabled = False
        self.vector_store_cfg.save(cfg)
        self._build_vector_index()
        return self.vector_public()

    def vector_rebuild(self, progress=None) -> dict:
        if self.vector_index is None or not self.vector_index.ready:
            return {"ok": False, "error": "向量检索未启用或模型未就绪"}
        return self.vector_index.index_all(progress=progress)

    def vector_status(self) -> dict:
        return self.vector_public()

    # ---- 重排（P1） ----
    def rerank_public(self) -> dict:
        data = self.rerank_config.public_dict()
        r = self.reranker
        data.update({
            "ready": bool(r is not None and getattr(r, "ready", False)),
            "name": (getattr(r, "name", "") if r is not None else ""),
        })
        return data

    def save_rerank(self, data: dict) -> dict:
        from ai.rerank import build_reranker

        cfg = self.rerank_config
        for key in ("provider", "base_url", "model", "api_style", "model_dir"):
            if key in data and data.get(key) is not None:
                setattr(cfg, key, str(data.get(key) or "").strip())
        if data.get("api_key"):
            cfg.api_key = str(data.get("api_key") or "").strip()
        if "enabled" in data and data.get("enabled") is not None:
            cfg.enabled = bool(data.get("enabled"))
        if "max_candidates" in data and data.get("max_candidates"):
            cfg.max_candidates = max(1, int(data.get("max_candidates")))
        if "top_k" in data and data.get("top_k"):
            cfg.top_k = max(1, int(data.get("top_k")))
        if "use_system_proxy" in data and data.get("use_system_proxy") is not None:
            cfg.use_system_proxy = bool(data.get("use_system_proxy"))
        if "idle_unload_s" in data and data.get("idle_unload_s") is not None:
            cfg.idle_unload_s = max(0, int(data.get("idle_unload_s")))
        if "cache_size" in data and data.get("cache_size") is not None:
            cfg.cache_size = max(0, int(data.get("cache_size")))
        self.rerank_store.save(cfg)
        self.reranker = build_reranker(cfg)
        return self.rerank_public()

    def rerank_hits(self, query: str, hits: list[dict]) -> list[dict]:
        """对候选做一次精排；失败/未启用时原样返回。"""
        r = self.reranker
        cfg = self.rerank_config
        strong = [h for h in hits if str(h.get("tier") or "strong") != "weak"]
        weak = [h for h in hits if str(h.get("tier") or "strong") == "weak"]
        if r is None or not getattr(r, "ready", False) or len(strong) < 2:
            return hits
        n = min(len(strong), max(1, cfg.max_candidates))
        docs = [
            (str(h.get("title") or "") + "\n" + str(h.get("snippet_text") or ""))[:2000]
            for h in strong[:n]
        ]
        cache_key = hashlib.sha1(
            (query + "\x00" + "\x00".join(docs)).encode("utf-8", "ignore")
        ).hexdigest()
        scores = None
        if cfg.cache_size and cfg.cache_size > 0:
            with self._rerank_cache_lock:
                cached = self._rerank_cache.get(cache_key)
                if cached is not None and time.monotonic() - cached[0] < 300:
                    scores = list(cached[1])
                    self._rerank_cache.move_to_end(cache_key)
        if scores is None:
            try:
                scores = r.rerank(query, docs)
            except Exception:
                log.exception("rerank 失败，回退 P0 排序")
                return hits
            if len(scores) != n:
                return hits
            if cfg.cache_size and cfg.cache_size > 0:
                with self._rerank_cache_lock:
                    self._rerank_cache[cache_key] = (time.monotonic(), list(scores))
                    self._rerank_cache.move_to_end(cache_key)
                    while len(self._rerank_cache) > int(cfg.cache_size):
                        self._rerank_cache.popitem(last=False)
        ranked = sorted(zip(strong[:n], scores), key=lambda pair: pair[1], reverse=True)
        out: list[dict] = []
        for hit, score in ranked:
            item = dict(hit)
            item["reranked"] = True
            item["rerank_score"] = round(float(score), 4)
            out.append(item)
        out.extend(strong[n:])
        out.extend(weak)
        return out[: max(1, cfg.top_k)]

    def rerank_test(self) -> dict:
        r = self.reranker
        if r is None or not getattr(r, "ready", False):
            raise ValueError("rerank 未启用或未就绪")
        scores = r.rerank("文件锁怎么加锁", ["fcntl 文件锁加锁示例", "Vue 组件通信 props emit"])
        return {"ok": True, "scores": [round(float(x), 4) for x in scores], "name": getattr(r, "name", "")}

    # ---- 查询改写（P2） ----
    def query_public(self) -> dict:
        data = self.query_config.public_dict()
        data.update({"ready": bool(self.rewriter.ready), "ai_ready": bool(self.ai_config.ready)})
        return data

    def save_query(self, data: dict) -> dict:
        from ai.query_rewrite import QueryRewriter

        cfg = self.query_config
        if "enabled" in data and data.get("enabled") is not None:
            cfg.enabled = bool(data.get("enabled"))
        if "max_queries" in data and data.get("max_queries") is not None:
            cfg.max_queries = max(1, min(4, int(data.get("max_queries"))))
        if "timeout_s" in data and data.get("timeout_s") is not None:
            cfg.timeout_s = max(1.0, min(15.0, float(data.get("timeout_s"))))
        if "system_prompt" in data and data.get("system_prompt") is not None:
            from ai.query_rewrite import DEFAULT_QUERY_PROMPT

            prompt = str(data.get("system_prompt") or "").strip()[:2000]
            cfg.system_prompt = prompt or DEFAULT_QUERY_PROMPT
        self.query_store.save(cfg)
        self.rewriter = QueryRewriter(cfg, self.ai_config)
        return self.query_public()

    def search_local_expanded(self, query: str, topk: int = 8) -> list:
        """本地搜索：启用查询计划时先拆词，再软 AND/OR 召回；失败回退原查询。"""
        if self.rewriter.ready:
            try:
                plan = self.rewriter.plan(query)
            except Exception:
                log.exception("检索计划生成失败，回退原查询")
                plan = None
            if plan is not None:
                from ai.query_plan import evaluate_plan

                hits = evaluate_plan(self.searcher, plan, topk)
                if hits:
                    return hits
        return self.searcher.search(query, topk)

    def _merge_expanded(self, lists: list, topk: int) -> list:
        """改写结果只追加、不重排原查询结果；base 永远在前。"""
        base = list(lists[0])
        seen = {int(h.doc_id) for h in base}
        out = base[:topk]
        for hits in lists[1:]:
            for hit in hits:
                if len(out) >= topk:
                    return out
                key = int(hit.doc_id)
                if key in seen:
                    continue
                seen.add(key)
                hit.route = "expanded"
                out.append(hit)
        return out

    def _rrf_merge(self, lists: list, topk: int) -> list:
        base_ids = {h.doc_id for h in lists[0]}
        scores: dict[int, float] = {}
        best: dict[int, object] = {}
        for hits in lists:
            for rank, hit in enumerate(hits):
                key = int(hit.doc_id)
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank + 1)
                if key not in best:
                    best[key] = hit
        out = []
        for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:topk]:
            hit = best[key]
            hit.score = float(score)
            if key not in base_ids:
                hit.route = "expanded"
            out.append(hit)
        return out

    # ---- 检索与查看 ----
    def search(self, query: str, topk: int = 8) -> list[dict]:
        hits = [h.to_dict() for h in self.search_local_expanded(query, topk)]
        return self.rerank_hits(query, hits)

    def get_doc_text(self, doc_id: int, max_chars: int = 20000) -> str:
        """全文查看：仅限已索引文档；此刻再复核排除规则与文件存在（红线⑤）。"""
        doc = self._verified_doc(doc_id)
        text = self.searcher.get_doc_text(doc_id, max_chars) or ""
        return text

    def _verified_doc(self, doc_id: int) -> dict:
        doc = self.db.get_doc_by_id(doc_id)
        if doc is None or self.connector_of(doc.mount_id) is None:
            raise LookupError("文档不存在或未参与共享")
        abs_path = Path(doc.abs_path)
        # 先查排除（红线⑤：排除即不可得，给出明确 403 与原因），再查索引状态
        reason = self.engine.is_excluded(abs_path, doc.rel_path, is_dir=False, size=doc.size)
        if reason:
            raise PermissionError("文档已被排除，全文不可得（红线⑤）")
        if doc.status != "indexed":
            raise LookupError("文档未参与共享（状态: " + doc.status + "）")
        if not abs_path.exists():
            raise LookupError("文件已不存在")
        return {
            "doc_id": doc.doc_id,
            "abs_path": doc.abs_path,
            "rel_path": doc.rel_path,
            "title": doc.title,
            "format": doc.format,
        }

    # ---- 打开文件（设计 §9 双平台写法） ----
    def open_location(self, doc_id: int) -> tuple[bool, str, str]:
        """在文件管理器中定位并高亮选中。返回 (ok, how, hint)。"""
        doc = self._verified_doc(doc_id)
        path = Path(doc["abs_path"])
        if os.name == "nt":
            # Windows：explorer /select,path 高亮选中该文件
            try:
                subprocess.Popen(["explorer", "/select," + str(path)])
                return True, "explorer /select", ""
            except OSError as exc:
                return False, "", "无法打开所在位置: " + str(exc)
        # Linux：优先 DBus FileManager1.ShowItems 高亮，回退 xdg-open 所在目录
        try:
            subprocess.Popen(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.freedesktop.FileManager1",
                    "--type=method_call",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    "array:string:" + path.as_uri(),
                    "string:",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, "dbus FileManager1.ShowItems", ""
        except FileNotFoundError:
            try:
                subprocess.Popen(["xdg-open", str(path.parent)])
                return True, "xdg-open (所在目录)", ""
            except OSError as exc:
                return False, "", "无法打开所在目录: " + str(exc)

    def open_file(self, doc_id: int) -> tuple[bool, str, str, list]:
        """用系统默认程序直接打开原文件（不改动原文件）。返回 (ok, how, hint, alternatives)。

        Windows（M2-FIX3/FIX4/FIX6）：先查注册表关联（非空白 + 命令可解析），
        有关联 startfile 直开；无关联/失效时不再弹系统对话框（用户桌面不可见），
        返回应用内 alternatives（VS Code/记事本）由 UI 渲染「用 XX 打开」按钮。
        """
        doc = self._verified_doc(doc_id)
        path = Path(doc["abs_path"])
        if os.name == "nt":
            return _windows_open_file(path)
        try:
            subprocess.Popen(["xdg-open", str(path)])
            return True, "xdg-open", "", []
        except OSError as exc:
            return False, "", "无法打开文件: " + str(exc), []

    def open_with(self, doc_id: int, app: str) -> tuple[bool, str, str]:
        """用指定应用打开（M2-FIX6）：app ∈ {vscode, notepad}。返回 (ok, how, hint)。"""
        doc = self._verified_doc(doc_id)
        path = Path(doc["abs_path"])
        if os.name != "nt":
            try:
                subprocess.Popen(["xdg-open", str(path)])
                return True, "xdg-open", ""
            except OSError as exc:
                return False, "", "无法打开文件: " + str(exc)
        alts = {a["app"]: a for a in _windows_alternatives()}
        if app not in alts:
            return False, "", "该应用不可用（未安装或未找到）: " + app
        exe = alts[app]["path"]
        try:
            subprocess.Popen([exe, str(path)])
            return True, alts[app]["label"], ""
        except OSError as exc:
            return False, "", "打开失败: " + str(exc)


def _windows_has_association(path: Path) -> bool:
    r"""检查 Windows 文件关联是否真实可用（M2-FIX3/FIX4）。

    判定规则（M2-FIX4 用户反馈：键存在但值为空串时 os.startfile 静默无效果、
    回退永不触发）：
        1) ProgId 取值：用户级 HKCU\...\FileExts\.ext\UserChoice 的 ProgId 优先，
           其次机器级 HKCR\.ext 默认值；值必须为非空白字符串（空串/纯空白=无关联）；
        2) 可解析校验：ProgId 的 shell\open\command 默认值必须存在且非空白，
           其可执行部分（首个引号串或首个 token）要么含环境变量（%...%）无法静态
           校验、要么 exe 存在（裸文件名走 PATH 查找）——不可解析视为无关联，
           回退「打开方式」对话框。
    """
    ext = path.suffix.lower()
    if not ext:
        return False
    try:
        import winreg

        progid: Optional[str] = None
        # 1) 用户级覆盖（键存在但 ProgId 为空串 -> 无效，继续查机器级）
        try:
            user_choice_key = (
                "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\"
                + ext
                + "\\UserChoice"
            )
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, user_choice_key) as key:
                value, _ = winreg.QueryValueEx(key, "ProgId")
                if isinstance(value, str) and value.strip():
                    progid = value.strip()
        except OSError:
            pass
        # 2) 机器级默认（存在但为空串 -> 无关联）
        if not progid:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as key:
                value, _ = winreg.QueryValueEx(key, "")
                if isinstance(value, str) and value.strip():
                    progid = value.strip()
        if not progid:
            return False
        # 3) shell\open\command 可解析校验
        return _progid_command_usable(progid)
    except (OSError, ImportError):
        # 注册表项缺失 / winreg 不可用（非 Windows 平台直接调该函数时）
        return False


def _progid_command_usable(progid: str) -> bool:
    r"""校验 HKCR\<progid>\shell\open\command：存在、非空白、可执行部分可解析。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, progid + "\\shell\\open\\command"
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return False
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    m = re.match(r'^"([^"]+)"', cmd.strip()) or re.match(r"^(\S+)", cmd.strip())
    if not m:
        return False
    exe_str = m.group(1)
    if "%" in exe_str:
        return True  # 含环境变量（如 %SystemRoot%）：静态不可校验，视为可用
    exe = Path(exe_str)
    if exe.parent == Path("."):
        return shutil.which(exe.name) is not None  # 裸文件名：走 PATH 查找
    return exe.exists()


def _expand_env(value: str) -> str:
    """展开 %VAR% 环境变量（注册表命令串解析用）。"""
    return os.path.expandvars(value.strip())


def _find_vscode_exe() -> Optional[str]:
    """从注册表 ProgId 解析 VS Code 可执行文件；失败回退常见安装路径。

    M2-FIX6：系统「打开方式」选择框在用户桌面不可见，改为应用内提供替代应用。
    """
    cmd_candidates: list[str] = []
    try:
        import winreg

        for sub in (
            r"Applications\Code.exe\shell\open\command",
            r"Applications\VSCode.exe\shell\open\command",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as key:
                    cmd, _ = winreg.QueryValueEx(key, "")
                if isinstance(cmd, str):
                    cmd_candidates.append(cmd)
            except OSError:
                continue
    except ImportError:
        pass
    # 注册表命令串：解析出可执行文件部分
    for cmd in cmd_candidates:
        m = re.match(r'^"([^"]+)"', cmd.strip()) or re.match(r"^(\S+)", cmd.strip())
        if not m:
            continue
        exe = _expand_env(m.group(1))
        if exe and Path(exe).exists():
            return exe
    # 常见安装路径：直接路径（可能含空格），不做命令解析
    for direct in (
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe"),
        str(Path(os.environ.get("ProgramFiles", "")) / "Microsoft VS Code" / "Code.exe"),
    ):
        exe = _expand_env(direct)
        if exe and Path(exe).exists():
            return exe
    return None


def _windows_alternatives() -> list[dict]:
    """无关联时的应用内打开方案（M2-FIX6）：VS Code（ProgId 解析）+ 记事本。"""
    alts: list[dict] = []
    vscode = _find_vscode_exe()
    if vscode:
        alts.append({"app": "vscode", "label": "用 VS Code 打开", "path": vscode})
    notepad = _expand_env("%SystemRoot%\\System32\\notepad.exe")
    if not Path(notepad).exists():
        notepad = "notepad.exe"  # 依赖 PATH 查找
    alts.append({"app": "notepad", "label": "用记事本打开", "path": notepad})
    return alts


def _windows_open_file(path: Path) -> tuple[bool, str, str, list]:
    """Windows 打开链路（M2-FIX6，可独立单测）：

    有关联 -> os.startfile 直开；
    无关联/关联失效 -> 不再依赖系统「打开方式」弹窗（实测用户桌面不可见、
    openas 抛 1155），返回应用内 alternatives 由 UI 渲染按钮。
    """
    if _windows_has_association(path):
        try:
            os.startfile(str(path))
            return True, "系统默认程序", "", []
        except OSError:
            pass  # 关联存在但失效：走 alternatives
    alternatives = _windows_alternatives()
    hint = (
        "该类型文件未关联默认程序，请选择打开方式"
        if alternatives
        else "该类型文件未关联默认程序，且未找到可用应用"
    )
    return True, "无默认关联", hint, alternatives


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="凿壁 ChiselWall 本地节点（M2）")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_CONFIG_DIR / "chiselwall.db"),
        help="索引库路径（红线②：默认本机用户目录，不放挂载目录内）",
    )
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR), help="配置目录（token 等）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（设计 §11：仅本机）")
    parser.add_argument("--port", type=int, default=8317, help="监听端口")
    parser.add_argument("--debounce", type=float, default=DEBOUNCE_DEFAULT, help="watchdog 防抖秒数（3-5）")
    parser.add_argument("--cpu-threshold", type=float, default=CPU_THRESHOLD_DEFAULT, help="空闲调度 CPU 阈值（%）")
    parser.add_argument("--trigram", action="store_true", help="开启 trigram 兜底表（默认关；仅小文档写入）")
    parser.add_argument("--trigram-max-chars", type=int, default=100_000, help="trigram 仅对总字符数 ≤ 该值的文档写入")
    parser.add_argument("--relay-url", default="", help="relay WebSocket 地址（如 ws://127.0.0.1:18760/ws）")
    parser.add_argument("--relay-nick", default="", help="本机昵称（relay 在线标识）")
    parser.add_argument("--relay-group", action="append", default=None, help="加入的提问组（可多次；缺省全员组）")
    parser.add_argument("--relay-token", default="", help="relay 鉴权 token")
    parser.add_argument("--ask-deadline-ms", type=int, default=5000, help="随手问收口超时（毫秒）")
    parser.add_argument("--answer-budget-ms", type=int, default=800, help="应答本地检索时间预算（毫秒）")
    parser.add_argument("--ai-base-url", default="", help="OpenAI 兼容 base_url（如 https://api.deepseek.com）")
    parser.add_argument("--ai-key", default="", help="模型 API key（也可在设置页填写）")
    parser.add_argument("--ai-model", default="", help="模型名（如 deepseek-chat）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器（默认启动后打开）")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    relay_url = args.relay_url.strip()
    config = Config(
        db_path=Path(args.db).expanduser(),
        config_dir=Path(args.config_dir).expanduser(),
        debounce=args.debounce,
        cpu_threshold=args.cpu_threshold,
        trigram_enabled=args.trigram,
        trigram_max_chars=args.trigram_max_chars,
        relay_enabled=bool(relay_url),
        relay_url=relay_url,
        relay_nick=args.relay_nick.strip() or "我",
        relay_groups=list(args.relay_group or ["全员组"]),
        relay_token=args.relay_token.strip(),
        ask_deadline_ms=max(500, int(args.ask_deadline_ms)),
        answer_budget_ms=max(100, int(args.answer_budget_ms)),
        ai_base_url=args.ai_base_url.strip(),
        ai_key=args.ai_key.strip(),
        ai_model=args.ai_model.strip(),
    )
    agent = NodeAgent(config)
    from api.server import create_app, load_or_create_token

    token = load_or_create_token(config.config_dir)
    app = create_app(agent, token)
    agent.start()
    url = "http://%s:%s" % (args.host, args.port)
    print("凿壁 ChiselWall 本地节点: %s" % url)
    print("API token（请求头 X-Chisel-Token，首次使用请在 UI 弹窗中输入）:", token)

    if not args.no_browser:
        import threading
        import webbrowser

        def _open() -> None:
            try:
                webbrowser.open(url)
            except Exception:
                log.exception("打开浏览器失败")

        threading.Timer(1.5, _open).start()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
