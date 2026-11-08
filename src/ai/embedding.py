"""向量嵌入提供方（M7）：本地 ONNX（bge-small-zh-v1.5）或 OpenAI 兼容 /embeddings。"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional, Protocol

import httpx

log = logging.getLogger("chiselwall.vector")

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


@dataclass
class VectorConfig:
    enabled: bool = False
    provider: str = "local"          # local | openai
    model_dir: str = ""              # 本地 ONNX 目录（空 = 默认 models/bge-small-zh-v1.5）
    base_url: str = ""               # OpenAI 兼容 /embeddings 的 base_url
    model: str = ""                  # embedding 模型名（openai 模式）
    api_key: str = ""
    dim: int = 512
    top_k: int = 20
    use_system_proxy: bool = False
    # P0 噪声收敛：纯向量命中的门槛（低于阈值不展示）
    min_similarity: float = 0.62
    vector_only_max: int = 3
    require_lexical: bool = True
    backend: str = "sqlite-vec"

    @classmethod
    def from_dict(cls, data: dict) -> "VectorConfig":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model_dir": self.model_dir,
            "base_url": self.base_url,
            "model": self.model,
            "has_key": bool(self.api_key),
            "masked_key": ("****" + self.api_key[-4:]) if len(self.api_key) > 4 else "",
            "dim": self.dim,
            "top_k": self.top_k,
            "use_system_proxy": self.use_system_proxy,
            "min_similarity": self.min_similarity,
            "vector_only_max": self.vector_only_max,
            "require_lexical": self.require_lexical,
            "backend": self.backend,
        }


class VectorConfigStore:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "vector.json"
        self.config_dir = config_dir

    def load(self) -> VectorConfig:
        if not self.path.exists():
            return VectorConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("读取 vector.json 失败，使用默认配置")
            return VectorConfig()
        return VectorConfig.from_dict(data) if isinstance(data, dict) else VectorConfig()

    def save(self, cfg: VectorConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def default_model_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "models" / "bge-small-zh-v1.5",
        Path(getattr(__import__("sys"), "_MEIPASS", here.parents[2])) / "models" / "bge-small-zh-v1.5",
        Path.cwd() / "models" / "bge-small-zh-v1.5",
    ]
    for c in candidates:
        if (c / "onnx" / "model_quantized.onnx").exists():
            return c
    return candidates[0]


class LocalOnnxEmbeddings:
    """bge-small-zh-v1.5 ONNX int8：CLS pooling + L2 归一化；线程内单例。"""

    def __init__(self, model_dir: Optional[str] = None, dim: int = 512) -> None:
        self.dir = Path(model_dir) if model_dir else default_model_dir()
        self.dim = dim
        self._lock = threading.Lock()
        self._tok = None
        self._sess = None
        self._np = None
        self._error = ""

    @property
    def model_id(self) -> str:
        return "local:%s:%d" % (self.dir.name, self.dim)

    @property
    def ready(self) -> bool:
        return (self.dir / "onnx" / "model_quantized.onnx").exists() and (
            self.dir / "tokenizer.json"
        ).exists()

    def _load(self) -> None:
        if self._sess is not None:
            return
        with self._lock:
            if self._sess is not None:
                return
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._np = np
            self._tok = Tokenizer.from_file(str(self.dir / "tokenizer.json"))
            self._tok.enable_truncation(max_length=512)
            self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.enable_cpu_mem_arena = False  # 降低常驻/峰值内存（4C8G 友好）
            self._sess = ort.InferenceSession(
                str(self.dir / "onnx" / "model_quantized.onnx"),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            log.info("本地 embedding 模型已加载：%s", self.dir)

    MAX_CHARS = 1500   # 单条截断（防止超长 CSV/日志撑爆内存）
    BATCH = 8

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        self._load()
        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            out.extend(self._embed_batch(texts[start:start + self.BATCH], query=query))
        return out

    def _embed_batch(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        np = self._np
        payload = [
            ((QUERY_PREFIX if query else "") + str(t or "")[: self.MAX_CHARS]) for t in texts
        ]
        encs = [self._tok.encode(t).ids for t in payload]
        maxlen = max((len(x) for x in encs), default=1)
        ids = np.zeros((len(encs), maxlen), dtype=np.int64)
        mask = np.zeros((len(encs), maxlen), dtype=np.int64)
        for i, x in enumerate(encs):
            ids[i, : len(x)] = x
            mask[i, : len(x)] = 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if any(i.name == "token_type_ids" for i in self._sess.get_inputs()):
            feed["token_type_ids"] = np.zeros_like(ids)
        try:
            with self._lock:
                hidden = self._sess.run(None, feed)[0]
        except Exception:
            if len(payload) > 1:
                log.warning("批量嵌入失败，改为逐条重试（batch=%d）", len(payload))
                out: list[list[float]] = []
                for text in payload:
                    out.extend(self._embed_batch([text]))
                return out
            raise
        vec = hidden[:, 0, :]
        norm = np.linalg.norm(vec, axis=1, keepdims=True)
        vec = vec / np.maximum(norm, 1e-9)
        return vec.astype("float32").tolist()


class OpenAIEmbeddings:
    """OpenAI 兼容 /embeddings（内网模型/OpenAI/Ollama）。"""

    def __init__(self, base_url: str, model: str, api_key: str = "", *, dim: int = 512,
                 use_system_proxy: bool = False, timeout_s: float = 30.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.use_system_proxy = use_system_proxy
        self.timeout_s = timeout_s

    @property
    def model_id(self) -> str:
        return "openai:%s:%d" % (self.model, self.dim)

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.model)

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not self.ready:
            raise ValueError("embedding 服务未配置（base_url/model）")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        url = self.base_url + "/embeddings"
        with httpx.Client(timeout=self.timeout_s, trust_env=self.use_system_proxy) as client:
            resp = client.post(url, json={"model": self.model, "input": list(texts)}, headers=headers)
        if resp.status_code >= 400:
            raise ValueError("embedding 服务返回 %s: %s" % (resp.status_code, resp.text[:200]))
        data = resp.json()
        items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
        vecs = [list(map(float, it["embedding"])) for it in items]
        if len(vecs) != len(texts):
            raise ValueError("embedding 返回条数不匹配")
        return vecs


class FakeEmbeddings:
    """测试用：字符 n-gram 哈希到固定维度（确定性、无需模型）。"""

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim
        self.ready = True

    @property
    def model_id(self) -> str:
        return "fake:%d" % self.dim

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for i, ch in enumerate(t):
                idx = int(hashlib.md5((ch + str(i % 3)).encode()).hexdigest(), 16) % self.dim
                vec[idx] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out
