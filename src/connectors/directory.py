"""DirectoryConnector：挂载本地目录或单文件（设计 §6）。

支持格式（M2）：txt / md / docx / xlsx / pdf / csv。
    - txt/md/csv：charset-normalizer 编码探测（UTF-8-sig/GBK/UTF-16 兜底），md 剥离 YAML frontmatter；
    - docx：python-docx（段落 + 表格），加密/损坏 → ParseError；
    - xlsx：openpyxl read_only（多 sheet，公式读缓存值），加密/损坏 → ParseError；
    - pdf ：pypdf 逐页抽文本，加密/无文本层（扫描件）→ ParseError（OCR 明确不做）；
    - .doc/.xls/.ppt 旧格式：跳过并提示（原因记入排除/失败列表）。

红线：
    - 只读：所有文件以 'rb' 打开，绝不写回；
    - 不跟随符号链接（防循环、防越出挂载点）；
    - 单文件解析失败抛 ParseError，由 indexer 隔离记录，不阻断整体索引（红线④）；
    - 单文档文本上限 5MB（截断 + truncated 标记，评审结论④）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, Optional

from .base import Connector, DocMeta, ParsedDoc, ParseError, SUPPORTED_EXTS
from .excludes import ExclusionEngine, looks_binary
from .yuanbao import classify_json
from .yuanbao import doc_text as yuanbao_doc_text
from .yuanbao import doc_title as yuanbao_doc_title

# 读取字节上限 16MB：远超 5MB 文本上限所需，防超大文件撑爆内存（截断后再解码）
_READ_BYTE_CAP = 16 * 1024 * 1024
# 编码探测采样：前 1MB 足够判定编码（charset-normalizer 不需要全量）
_ENCODING_SAMPLE = 1 * 1024 * 1024

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\n---\r?\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """剥离 Markdown YAML frontmatter（Obsidian 笔记常见），正文索引不受元数据干扰。"""
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


def decode_text(data: bytes, path: Path) -> str:
    """编码探测解码：charset-normalizer 优先，UTF-8-sig/GBK/UTF-16 兜底。"""
    if looks_binary(data):
        raise ParseError("内容为二进制（伪装为文本扩展名）", code="binary_content")
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data[:_ENCODING_SAMPLE]).best()
        if best is not None and best.encoding:
            try:
                return data.decode(best.encoding)
            except (UnicodeDecodeError, LookupError):
                pass
    except ImportError:  # 依赖缺失时退化为固定编码尝试（极端容错）
        pass
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ParseError(f"无法识别文本编码: {path.name}", code="decode_error")


def _read_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(_READ_BYTE_CAP)


class DirectoryConnector(Connector):
    """本地目录 / 单文件挂载的连接器。"""

    kind = "directory"

    def __init__(
        self,
        root: Path,
        *,
        text_cap_chars: int = 5_000_000,
        engine: Optional[ExclusionEngine] = None,
    ) -> None:
        super().__init__(root, text_cap_chars=text_cap_chars)
        self.engine: ExclusionEngine = engine if engine is not None else ExclusionEngine()
        self._is_file_root: bool = self.root.is_file()

    # ---- 协议 ----
    def watch_allowed(self, path: Path) -> bool:
        if not super().watch_allowed(path):
            return False
        if self._is_file_root or path.resolve() == self.root:
            return True
        if path.is_symlink():
            return False
        rel = self._rel(path.resolve())
        return self.engine.is_excluded(path.resolve(), rel, is_dir=True) is None

    def formats(self) -> frozenset[str]:
        return SUPPORTED_EXTS

    def default_excludes(self) -> list[str]:
        return []  # 目录连接器无额外默认排除（内置规则在 ExclusionEngine）

    def list_docs(self, under: Optional[str] = None) -> Iterator[DocMeta]:
        """遍历挂载点产出候选文档。

        被排除的文件仍会产出（excluded=True 带原因，供 indexer 记录状态与统计）；
        被排除的目录整棵剪枝不进入（效率 + 隐私：红线⑤）。
        """
        if self._is_file_root:
            yield self.meta_of(self.root.name)
            return
        base = self.root if under is None else self.resolve(under)
        if not base.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d
                for d in dirnames
                if self.engine.is_excluded(Path(dirpath) / d, self._rel(Path(dirpath) / d), is_dir=True)
                is None
            ]
            for fn in filenames:
                abs_path = Path(dirpath) / fn
                if abs_path.is_symlink():
                    continue  # 不跟随符号链接：防循环、防越出挂载点
                try:
                    meta = self.meta_of(self._rel(abs_path), abs_path)
                except ParseError:
                    continue  # 扫描期间文件消失等竞态：跳过，下次事件再处理
                # 支持格式正常产出；不支持格式以 excluded 产出（计入排除统计）
                yield meta

    def meta_of(self, rel_path: str, abs_path: Optional[Path] = None) -> DocMeta:
        """构建文档元信息（含排除判定）；文件不可访问时抛 ParseError。

        不支持的格式（.json/.log 等非 M2 支持清单）进入排除轨道
        （excluded=True, reason=unsupported_format），计入 stats.excluded，
        用户可在排除列表看到原因——M2-FIX2 用户验收反馈（原为静默跳过）。
        """
        abs_path = abs_path or self.resolve(rel_path)
        try:
            st = abs_path.stat()
        except OSError as exc:
            raise ParseError(f"无法访问文件: {exc}", code="missing") from exc
        name = abs_path.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        rel = self.norm_rel(rel_path)
        reason = self.engine.is_excluded(abs_path, rel, is_dir=abs_path.is_dir(), size=st.st_size)
        title = abs_path.stem
        if reason is None and not abs_path.is_dir():
            if ext == "json":
                # 对话导出 JSON 结构识别（M2.5 元宝 / M3 YB 通用结构）：
                # invalid（语法错误）不排除，交由 read_doc 抛 ParseError -> 失败列表（YB-07）；
                # 合法但非对话结构 -> 不支持格式排除轨道；
                # md 孪生去重：同 basename 已有 .md 时只索引 .md，json 记 duplicate_md_twin
                kind, parsed = classify_json(abs_path)
                if kind == "invalid":
                    pass  # 让 read_doc 报错 -> 进入失败列表（红线④隔离）
                elif kind == "not_chat":
                    reason = "不支持的格式: .json（非对话导出结构）"
                elif abs_path.with_suffix(".md").exists():
                    reason = "duplicate_md_twin: 同 basename 已有 .md，仅索引 .md"
                else:
                    title = yuanbao_doc_title(parsed) or abs_path.stem
            elif ext not in SUPPORTED_EXTS:
                # 未知扩展名/无扩展名：进入排除轨道而非静默跳过（红线⑤：排除即不可见）
                reason = "不支持的格式: ." + ext if ext else "不支持的格式: 无扩展名"
        return DocMeta(
            rel_path=rel,
            abs_path=abs_path,
            title=title,
            format=ext,
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
        """解析单个文档为纯文本。被排除的文档拒绝读取（红线⑤）。"""
        abs_path = self.resolve(rel_path)
        meta = self.meta_of(rel_path, abs_path)
        if meta.excluded:
            raise ParseError(f"文档被排除，不可读取: {meta.exclude_reason}", code="excluded")
        ext = meta.ext
        try:
            if ext in ("txt", "md"):
                text = decode_text(_read_bytes(abs_path), abs_path)
                if ext == "md":
                    text = strip_frontmatter(text)
            elif ext == "csv":
                text = decode_text(_read_bytes(abs_path), abs_path)
            elif ext == "docx":
                text = self._read_docx(abs_path)
            elif ext == "xlsx":
                text = self._read_xlsx(abs_path)
            elif ext == "pdf":
                text = self._read_pdf(abs_path)
            elif ext == "json":
                kind, parsed = classify_json(abs_path)
                if kind == "invalid":
                    raise ParseError("JSON 解析失败（语法错误/编码异常）", code="json_error")
                if kind == "not_chat":
                    raise ParseError("不支持的格式: .json（非对话导出结构）", code="unsupported")
                text = yuanbao_doc_text(parsed)
            else:
                raise ParseError(f"不支持的格式: .{ext}", code="unsupported")
        except ParseError:
            raise
        except Exception as exc:
            # 红线④：解析失败隔离——库的意外异常一律转 ParseError 交由 indexer 记录
            raise ParseError(f"解析失败({exc.__class__.__name__}): {exc}", code="parse_error") from exc
        text, truncated = self._cap_text(text)
        if not text.strip():
            raise ParseError("无文本内容（空文档/扫描件 PDF），OCR 明确不做", code="empty")
        return ParsedDoc(meta=meta, text=text, truncated=truncated)

    # ---- 各格式解析（按需惰性 import，未安装对应库时只影响该格式） ----
    @staticmethod
    def _read_docx(path: Path) -> str:
        import docx

        try:
            doc = docx.Document(str(path))
        except Exception as exc:
            raise ParseError("无法打开 docx（加密/损坏），跳过", code="docx_open") from exc
        parts: list[str] = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)

    @staticmethod
    def _read_xlsx(path: Path) -> str:
        import openpyxl

        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        except Exception as exc:
            raise ParseError("无法打开 xlsx（加密/损坏），跳过", code="xlsx_open") from exc
        parts: list[str] = []
        try:
            for ws in wb.worksheets:
                parts.append(f"# {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    cells = ["" if c is None else str(c).strip() for c in row]
                    if any(cells):
                        parts.append("\t".join(cells))
        finally:
            wb.close()
        return "\n".join(parts)

    def _read_pdf(self, path: Path) -> str:
        from pypdf import PdfReader

        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise ParseError("无法打开 PDF（损坏），跳过", code="pdf_open") from exc
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
            if reader.is_encrypted:
                raise ParseError("加密 PDF，跳过", code="encrypted")
        parts: list[str] = []
        used = 0
        for page in reader.pages:
            t = (page.extract_text() or "").strip()
            if t:
                parts.append(t)
                used += len(t)
                if used >= self.text_cap_chars:
                    break  # 已达文本上限，停止翻页（资源保护）
        if not parts:
            raise ParseError("无文本层（扫描件/图片型 PDF），OCR 明确不做", code="no_text")
        return "\n\n".join(parts)
