"""重排（rerank）框架（P1）：HTTP 服务与本地 ONNX cross-encoder，可配置切换。

设计目标：
- 提问端只做一次精排，被搜索端不参与；
- HTTP 服务兼容 Cohere/Jina/TEI 常见协议（auto 自动探测）；
- 本地模型通过 model.json 清单描述，换模型只改清单/目录，不改代码；
- 任何失败/超时自动回退到 P0 排序，不阻塞搜索。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("chiselwall.rerank")


@dataclass
class RerankConfig:
    enabled: bool = False
    provider: str = "http"          # http | local
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_style: str = "auto"         # auto | cohere | jina | tei
    model_dir: str = ""
    max_candidates: int = 20
    top_k: int = 5
    timeout_s: float = 10.0
    use_system_proxy: bool = False
    idle_unload_s: int = 120   # 本地模型空闲多久卸载（0=不卸载）
    cache_size: int = 256      # 查询+候选分数 LRU 缓存条数（0=关闭）

    @classmethod
    def from_dict(cls, data: dict) -> "RerankConfig":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_style": self.api_style,
            "model_dir": self.model_dir,
            "max_candidates": self.max_candidates,
            "top_k": self.top_k,
            "has_key": bool(self.api_key),
            "masked_key": ("****" + self.api_key[-4:]) if len(self.api_key) > 4 else "",
            "idle_unload_s": self.idle_unload_s,
            "cache_size": self.cache_size,
        }


class RerankConfigStore:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "rerank.json"
        self.config_dir = config_dir

    def load(self) -> RerankConfig:
        if not self.path.exists():
            return RerankConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取 rerank.json 失败")
            return RerankConfig()
        return RerankConfig.from_dict(data) if isinstance(data, dict) else RerankConfig()

    def save(self, cfg: RerankConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def default_rerank_models_dir() -> Path:
    here = Path(__file__).resolve()
    for base in (here.parents[2], Path(getattr(__import__("sys"), "_MEIPASS", here.parents[2]))):
        candidate = base / "models" / "rerank"
        if candidate.exists():
            return candidate
    return here.parents[2] / "models" / "rerank"


class HttpReranker:
    """Cohere/Jina/TEI 风格的 HTTP rerank 客户端。"""

    def __init__(self, cfg: RerankConfig) -> None:
        self.cfg = cfg

    @property
    def ready(self) -> bool:
        return bool(self.cfg.base_url.strip())

    @property
    def name(self) -> str:
        return "http:" + (self.cfg.model or "default")

    def _payload(self, style: str, query: str, docs: list[str]) -> dict:
        if style == "tei":
            return {"query": query, "texts": docs, "truncate": True}
        return {
            "model": self.cfg.model or "rerank",
            "query": query,
            "documents": docs,
            "top_n": len(docs),
        }

    def _parse(self, style: str, data) -> list[float]:
        scores = [0.0] * self._n
        items = data
        if isinstance(data, dict):
            items = data.get("results") or data.get("data") or data.get("scores")
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            raise ValueError("rerank 响应格式无法识别")
        for i, item in enumerate(items):
            if isinstance(item, dict):
                idx = int(item.get("index", i))
                score = item.get("relevance_score", item.get("score", 0.0))
            else:
                idx, score = i, item
            if 0 <= idx < len(scores):
                scores[idx] = float(score)
        return scores

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        self._n = len(docs)
        url = self.cfg.base_url.rstrip("/") + "/rerank"
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = "Bearer " + self.cfg.api_key
        styles = [self.cfg.api_style] if self.cfg.api_style not in ("", "auto") else ["cohere", "tei"]
        last_error = ""
        with httpx.Client(timeout=float(self.cfg.timeout_s), trust_env=self.cfg.use_system_proxy) as client:
            for style in styles:
                try:
                    resp = client.post(url, json=self._payload(style, query, docs), headers=headers)
                    if resp.status_code >= 400:
                        last_error = "HTTP %s: %s" % (resp.status_code, resp.text[:150])
                        continue
                    return self._parse(style, resp.json())
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    last_error = str(exc)
                    continue
        raise ValueError("rerank 服务调用失败：" + last_error)


class LocalCrossEncoderReranker:
    """本地 ONNX cross-encoder；由 model.json 清单驱动。"""

    def __init__(self, cfg: RerankConfig) -> None:
        self.cfg = cfg
        self.dir = Path(cfg.model_dir) if cfg.model_dir else default_rerank_models_dir()
        self._lock = threading.Lock()
        self._tok = None
        self._sess = None
        self._np = None
        self._last_use = 0.0
        self._timer: Optional[threading.Timer] = None
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> Optional[dict]:
        path = self.dir / "model.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取 rerank model.json 失败")
            return None
        return data if isinstance(data, dict) else None

    @property
    def ready(self) -> bool:
        if self._manifest is None:
            return False
        model_file = str(self._manifest.get("model_file") or "model.onnx")
        tokenizer = str(self._manifest.get("tokenizer") or "tokenizer.json")
        return (self.dir / model_file).exists() and (self.dir / tokenizer).exists()

    @property
    def name(self) -> str:
        return "local:" + (self.dir.name or "rerank")

    def _load(self) -> None:
        if self._sess is not None:
            return
        with self._lock:
            if self._sess is not None:
                return
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            m = self._manifest or {}
            self._np = np
            self._tok = Tokenizer.from_file(str(self.dir / str(m.get("tokenizer") or "tokenizer.json")))
            self._tok.enable_truncation(max_length=int(m.get("max_length") or 512))
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.enable_cpu_mem_arena = False  # 降低常驻内存（4C8G 友好）
            self._sess = ort.InferenceSession(
                str(self.dir / str(m.get("model_file") or "model.onnx")),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            log.info("本地 rerank 模型已加载：%s", self.dir)

    def _score_batch(self, query: str, docs: list[str]) -> list[float]:
        self._load()
        np = self._np
        m = self._manifest or {}
        names = list(m.get("inputs") or ["input_ids", "attention_mask"])
        max_len = int(m.get("max_length") or 512)
        encs = []
        for doc in docs:
            enc = self._tok.encode(query, str(doc or "")[:2000])
            ids = enc.ids[:max_len]
            encs.append(ids)
        width = max((len(x) for x in encs), default=1)
        input_ids = np.zeros((len(encs), width), dtype=np.int64)
        mask = np.zeros((len(encs), width), dtype=np.int64)
        for i, ids in enumerate(encs):
            input_ids[i, : len(ids)] = ids
            mask[i, : len(ids)] = 1
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        with self._lock:
            out = self._sess.run(None, feed)
        score_cfg = m.get("score") or {}
        idx = int(score_cfg.get("output", 0))
        values = out[min(idx, len(out) - 1)]
        arr = np.asarray(values)
        if arr.ndim >= 2:
            select = str(score_cfg.get("select") or "first")
            arr = arr[:, -1] if select == "last" else arr[:, 0]
        arr = arr.reshape(-1).astype("float64")
        activation = str(score_cfg.get("activation") or "none")
        if activation == "sigmoid":
            arr = 1.0 / (1.0 + np.exp(-arr))
        elif activation == "softmax" and arr.size:
            e = np.exp(arr - arr.max())
            arr = e / e.sum()
        return [float(x) for x in arr]

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        scores: list[float] = []
        for start in range(0, len(docs), 8):
            scores.extend(self._score_batch(query, docs[start:start + 8]))
        self._touch()
        return scores

    def _touch(self) -> None:
        """记录使用时间，并重置空闲卸载定时器。"""
        import time

        self._last_use = time.monotonic()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self.cfg.idle_unload_s and self.cfg.idle_unload_s > 0:
            timer = threading.Timer(float(self.cfg.idle_unload_s), self._maybe_unload)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _maybe_unload(self) -> None:
        import time

        with self._lock:
            if self._sess is None:
                return
            idle = time.monotonic() - self._last_use
            if idle < float(self.cfg.idle_unload_s or 0):
                return
            self._sess = None
            self._tok = None
            self._np = None
            log.info("本地 rerank 模型空闲 %.0fs，已卸载", idle)


def build_reranker(cfg: RerankConfig) -> Optional[object]:
    if not cfg.enabled:
        return None
    try:
        if cfg.provider == "local":
            return LocalCrossEncoderReranker(cfg)
        return HttpReranker(cfg)
    except Exception:
        log.exception("初始化 reranker 失败")
        return None