"""思源笔记 .sy 直读解析器（设计 §6、调研 03 §1；M3-1）。

.sy = Lute AST 序列化 UTF-8 JSON：
    - 根节点恒为 NodeDocument，正文递归 Children；
    - Type 决定节点类型（NodeParagraph/NodeHeading/NodeList/NodeCodeBlock/NodeText/
      NodeTextMark…），不凭 ID 判断；
    - 文本提取：NodeText.Data + NodeTextMark.TextMarkTextContent（Data 可省略，
      空 TextMark 不代表空文本）；inline-math 取 TextMarkInlineMathContent；
    - HeadingLevel 1-6 作 # 前缀结构提示；
    - base64 约定：Go []byte 字段（ListData.Marker、CodeBlockInfo）序列化为 base64
      字符串；Go byte 字段（BulletChar 等）是 int ASCII 码点；Data/TextMarkTextContent
      等内容保持原文不编码；
    - 文档标题取 Properties.title（权威键为小写 id/title/type/updated）。

兼容策略（调研 03 §1.7）：Spec ≥ "2" 一律尝试解析；未知 Type/字段忽略、仍递归
Children（升级后旧解析器仍能提取已知节点文本）；解析失败重试一次（与内核并行
写入竞争），仍失败记入失败列表。

加密笔记本：磁盘 .sy 为密文（不以 '{' 开头 / JSON 解析失败）→ 判定加密或损坏，
跳过并记录，不做解密（v1 不做内核 API 通道；官方 CLI 兜底转换仅登记接口，M3 不实现）。

红线：连接器对 .sy 只读，绝不写盘（产品原则「零迁移」）。
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Optional

from .base import Connector, DocMeta, ParsedDoc, ParseError
from .excludes import ExclusionEngine

# 思源工作区内不参与索引的目录（调研 03 §1.3）
EXCLUDED_WORKSPACE_DIRS = frozenset({
    ".siyuan", "assets", "templates", "widgets", "plugins", "storage", "repo", "history", "corrupted",
})


# ---- 纯文本提取（纯 Python 树遍历） ----
def _b64(value: Any) -> str:
    """Go []byte 字段：base64 字符串 -> 原文；非法/缺失返回空串。"""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _byte_field(value: Any) -> str:
    """Go byte 字段：int ASCII 码点 -> 字符（如 42='*'、88='X'）。"""
    if isinstance(value, int) and 0 <= value < 256:
        return chr(value)
    return str(value) if isinstance(value, str) else ""


def _children(node: dict) -> list[dict]:
    ch = node.get("Children")
    return [c for c in ch if isinstance(c, dict)] if isinstance(ch, list) else []


def _props(node: dict) -> dict:
    p = node.get("Properties")
    return p if isinstance(p, dict) else {}


def _extract_inline(node: dict) -> str:
    """内联节点取文本。"""
    typ = node.get("Type", "")
    if typ == "NodeText":
        return str(node.get("Data") or "")
    if typ == "NodeTextMark":
        mark_type = node.get("TextMarkType")
        if mark_type == "inline-math":
            return str(node.get("TextMarkInlineMathContent") or "")
        text = str(node.get("TextMarkTextContent") or "")
        if mark_type == "inline-memo":
            # M3 验收（SC-02）：行级备注内容在 TextMarkInlineMemoContent，需一并入索引
            memo = str(node.get("TextMarkInlineMemoContent") or "")
            return (text + " " + memo).strip() if memo else text
        return text
    return ""


def node_text(node: dict) -> str:
    """递归提取节点全部文本。

    - 内联节点直接取文本；块级容器以空行分隔子块；
    - NodeHeading 加 # 层级前缀、NodeList 加列表符号、NodeCodeBlock 加代码围栏；
    - 未知 Type：跳过自身、仍递归 Children（兼容未来 spec）。
    """
    if not isinstance(node, dict):
        return ""
    typ = node.get("Type", "")
    if typ in ("NodeText", "NodeTextMark", "NodeCodeBlockCode"):
        return _extract_inline(node) if typ != "NodeCodeBlockCode" else str(node.get("Data") or "")
    if typ == "NodeParagraph":
        return "".join(node_text(c) for c in _children(node))
    if typ == "NodeHeading":
        level = node.get("HeadingLevel")
        prefix = ("#" * int(level)) + " " if isinstance(level, int) and 1 <= level <= 6 else ""
        text = "".join(node_text(c) for c in _children(node)).strip()
        return (prefix + text) if text else ""
    if typ == "NodeList":
        # ListData.Typ：0=无序、1=有序、3=任务列表；Marker 是 base64、BulletChar 是码点
        list_data = node.get("ListData") or {}
        typ_marker = list_data.get("Typ")
        ordered = isinstance(typ_marker, int) and typ_marker == 1
        task = isinstance(typ_marker, int) and typ_marker == 3
        lines: list[str] = []
        for idx, child in enumerate(_children(node), start=1):
            line = node_text(child).strip()
            if not line:
                continue
            if task:
                lines.append("- [ ] " + line)
            elif ordered:
                lines.append(str(idx) + ". " + line)
            else:
                marker = _byte_field(list_data.get("BulletChar")) or _b64(list_data.get("Marker")) or "-"
                lines.append(marker + " " + line)
        return "\n".join(lines)
    if typ == "NodeListItem":
        return "".join(node_text(c) for c in _children(node)).strip()
    if typ == "NodeCodeBlock":
        lang = _b64(node.get("CodeBlockInfo"))
        code = "".join(node_text(c) for c in _children(node))
        return ("\x60\x60\x60" + lang + "\n" + code + "\n\x60\x60\x60") if code.strip() else ""
    if typ == "NodeImage":
        alt = _props(node).get("title") or ""
        return alt if alt else "[图片]"
    if typ == "NodeAudio":
        return "[音频]"
    if typ == "NodeVideo":
        return "[视频]"
    if typ == "NodeBlockquote":
        inner = "\n".join(node_text(c) for c in _children(node)).strip()
        return "\n".join("> " + line for line in inner.split("\n")) if inner else ""
    if typ == "NodeHTMLBlock":
        return str(node.get("Data") or "")
    if typ == "NodeMathBlock":
        return str(node.get("Data") or "")
    if typ == "NodeTable":
        return "\n".join(t for t in (node_text(c).strip() for c in _children(node)) if t)
    if typ == "NodeTableCell":
        return " | ".join(node_text(c).strip() for c in _children(node))
    # NodeDocument / NodeSuperBlock / NodeCallout / NodeTabs / NodeAttributeView / 未知类型：
    # 块容器语义——空行分隔子块，仍递归 Children
    return "\n\n".join(t for t in (node_text(c) for c in _children(node)) if t)


def parse_sy_file(path: Path) -> Optional[dict]:
    """读取 .sy：UTF-8（容忍 BOM）JSON 解析。

    加密笔记本磁盘密文：不以 '{' 开头直接判不可解析；JSON 解析失败同判。
    返回顶层 dict 或 None。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8-sig")  # 容忍 BOM；密文（非 UTF-8）在此直接失败
    except UnicodeDecodeError:
        return None
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None  # 加密笔记本密文 / 非 JSON：不可解析
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def sy_doc_text(data: dict) -> str:
    """文档正文：标题行（Properties.title，可搜）+ NodeDocument 递归提取的纯文本。"""
    parts: list[str] = []
    title = sy_doc_title(data)
    if title:
        parts.append("# " + title)
    body = node_text(data).strip()
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def sy_doc_title(data: dict) -> str:
    """文档标题：Properties.title（小写权威键）。"""
    title = _props(data).get("title")
    return str(title).strip() if title else ""


# ---- 连接器 ----
class SiyuanConnector(Connector):
    """思源工作空间直读连接器（调研 03 §1.6）：data/<boxID>/**/*.sy 只读索引。

    合法工作区校验：conf/conf.json 存在且含 kernelVersion。
    预留（§16，只登记不动工）：内核 HTTP API 实时块增强（127.0.0.1:6806 + Token）、
    官方 CLI「siyuan export md --id <id> -w <workspace>」兜底转换通道。
    """

    kind = "siyuan"

    def __init__(
        self,
        root: Path,
        *,
        text_cap_chars: int = 5_000_000,
        engine: Optional[ExclusionEngine] = None,
    ) -> None:
        super().__init__(root, text_cap_chars=text_cap_chars)
        self.engine = engine if engine is not None else ExclusionEngine()

    def formats(self) -> frozenset[str]:
        return frozenset({"sy"})

    def watch_root(self) -> Optional[Path]:
        data = self.root / "data"
        return data if self.root.is_dir() and data.is_dir() else None

    def watch_allowed(self, path: Path) -> bool:
        """只监视 data/ 下非编辑器内部目录（repo/history/conf/temp/assets 等不注册 watch）。"""
        data = self.root / "data"
        try:
            rel = path.resolve().relative_to(data.resolve())
        except (OSError, ValueError):
            return False
        if path.is_symlink():
            return False
        return not any(part in EXCLUDED_WORKSPACE_DIRS or part.startswith(".") for part in rel.parts)

    def default_excludes(self) -> list[str]:
        return [
            "data/.siyuan/**",
            "data/**/assets/**",
            "templates/**",
            "widgets/**",
            "plugins/**",
            "storage/**",
            "repo/**",
            "history/**",
            "corrupted/**",
        ]

    def is_valid_workspace(self) -> bool:
        """合法工作区校验（调研 03 §1.3）。

        官方口径：conf/conf.json 存在且含 kernelVersion。但用户真实工作区实测
        conf/conf.json 可解析却无 kernelVersion（该键在新版已迁至 temp/siyuan.db），
        故放宽为：conf/conf.json 存在且可解析为 dict 即视为合法；
        kernelVersion 仅作参考不复核（M3-1 真实数据验证结论）。
        """
        conf = self.root / "conf" / "conf.json"
        if not conf.exists():
            return False
        try:
            data = json.loads(conf.read_text(encoding="utf-8"))
            return isinstance(data, dict)
        except (OSError, ValueError):
            return False

    def list_docs(self, under: Optional[str] = None) -> Iterator[DocMeta]:
        if not self.is_valid_workspace():
            return
        data_dir = self.root / "data"
        if not data_dir.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(data_dir):
            dirnames[:] = [
                d for d in dirnames if d not in EXCLUDED_WORKSPACE_DIRS and not d.startswith(".")
            ]
            for fn in filenames:
                if not fn.endswith(".sy"):
                    continue
                abs_path = Path(dirpath) / fn
                if abs_path.is_symlink():
                    continue
                try:
                    yield self.meta_of(self._rel(abs_path), abs_path)
                except ParseError:
                    continue  # 扫描竞态：文件消失等，下次事件再处理

    def meta_of(self, rel_path: str, abs_path: Optional[Path] = None) -> DocMeta:
        abs_path = abs_path or self.resolve(rel_path)
        if abs_path.suffix.lower() != ".sy":
            # M3 验收（SC-10）：思源连接器只处理 .sy；assets/ 图片等非 .sy 文件不产生任何文档
            raise ParseError(f"非思源文档: {abs_path.name}", code="not_sy")
        try:
            st = abs_path.stat()
        except OSError as exc:
            raise ParseError(f"无法访问文件: {exc}", code="missing") from exc
        rel = self.norm_rel(rel_path)
        reason = self.engine.is_excluded(abs_path, rel, is_dir=abs_path.is_dir(), size=st.st_size)
        title = abs_path.stem
        if reason is None:
            data = parse_sy_file(abs_path)
            if data is not None:
                title = sy_doc_title(data) or abs_path.stem
        return DocMeta(
            rel_path=rel,
            abs_path=abs_path,
            title=title,
            format="sy",
            mtime=st.st_mtime,
            size=st.st_size,
            excluded=reason is not None,
            exclude_reason=reason,
        )

    def _rel(self, abs_path: Path) -> str:
        try:
            return self.norm_rel(str(abs_path.relative_to(self.root)))
        except ValueError:
            return abs_path.name

    def read_doc(self, rel_path: str) -> ParsedDoc:
        abs_path = self.resolve(rel_path)
        meta = self.meta_of(rel_path, abs_path)
        if meta.excluded:
            raise ParseError(f"文档被排除，不可读取: {meta.exclude_reason}", code="excluded")
        data: Optional[dict] = None
        for _attempt in range(2):
            # 与思源内核并行写入竞争：解析失败重试一次（调研 03 §1.6）
            data = parse_sy_file(abs_path)
            if data is not None:
                break
        if data is None:
            raise ParseError(
                "加密笔记本或损坏的 .sy（磁盘密文不解密；解锁后可用内核 API 索引，v1 不做）",
                code="encrypted_or_corrupt",
            )
        title = sy_doc_title(data) or abs_path.stem
        text, truncated = self._cap_text(sy_doc_text(data))
        if not text.strip():
            raise ParseError("空文档（无文本内容）", code="empty")
        return ParsedDoc(meta=replace(meta, title=title), text=text, truncated=truncated)
