"""元宝对话 / 通用对话导出 JSON 解析（M2.5 用户拍板；M3 验收 YB-01/04/07 扩展）。

识别条件（结构识别，避免误吃配置类 JSON）：
    A. 元宝原生导出：顶层 dict，含 convs 数组，各项带 speaker；
    B. 通用对话导出：顶层 dict，含 conversations 数组，各项带 messages（role/content|speech）。
合法 JSON 但不是对话结构 -> 「不支持格式」排除轨道；
非法 JSON（语法错误/编码异常）-> 交给 read_doc 抛 ParseError，进失败列表（YB-07）。

提取规则：
    - 标题：顶层 title；通用结构回退 conversations[0].title；
    - convs：每轮「speaker: speech」，speech 缺失回退 speechesV2 text；
    - conversations：每会话标题 + 各消息「role: content」，content 空时回退 speech；
    - md 孪生去重由目录连接器执行；失败隔离走 ParseError。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CHAT_KINDS = ("yuanbao", "generic")


def _load_json(path: Path) -> tuple[str, Optional[dict]]:
    """读取并分类：invalid / not_chat / yuanbao / generic。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return "invalid", None
    if not isinstance(data, dict):
        return "not_chat", data
    convs = data.get("convs")
    if isinstance(convs, list) and (not convs or all(isinstance(c, dict) and "speaker" in c for c in convs)):
        return "yuanbao", data
    conversations = data.get("conversations")
    if isinstance(conversations, list) and conversations and all(
        isinstance(c, dict) and isinstance(c.get("messages"), list) for c in conversations
    ):
        return "generic", data
    return "not_chat", data


def classify_json(path: Path) -> tuple[str, Optional[dict]]:
    """公开分类接口：invalid / not_chat / yuanbao / generic（目录连接器使用）。"""
    return _load_json(path)


def parse_yuanbao(path: Path) -> Optional[dict]:
    """兼容旧接口：识别为对话导出（元宝或通用）时返回数据，否则 None。"""
    kind, data = _load_json(path)
    return data if kind in CHAT_KINDS else None


def _speech_v2_text(conv: dict) -> str:
    """speechesV2[].content[] 中 type=text 的 msg 按序拼接（speech 缺失回退）。"""
    parts: list[str] = []
    for item in conv.get("speechesV2") or []:
        if not isinstance(item, dict):
            continue
        for piece in item.get("content") or []:
            if isinstance(piece, dict) and piece.get("type") == "text":
                msg = piece.get("msg")
                if msg:
                    parts.append(str(msg))
    return "\n".join(parts)


def conv_text(conv: dict) -> str:
    """元宝结构单轮：speaker + speech（speech 缺失回退 speechesV2 text msg）。"""
    speaker = str(conv.get("speaker") or "").strip()
    speech = conv.get("speech")
    if speech is None or not str(speech).strip():
        speech = _speech_v2_text(conv)
    text = str(speech or "").strip()
    if not text:
        return ""
    return (speaker + ": " + text) if speaker else text


def _generic_conv_text(conv: dict) -> str:
    """通用结构单会话：会话标题 + 各消息「role: content」，content 空回退 speech。"""
    parts: list[str] = []
    title = str(conv.get("title") or "").strip()
    if title:
        parts.append("# " + title)
    for msg in conv.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        content = msg.get("content")
        if content is None or not str(content).strip():
            content = msg.get("speech") or ""
        text = str(content or "").strip()
        if text:
            parts.append((role + ": " + text) if role else text)
    return "\n\n".join(parts)


def doc_title(data: dict) -> str:
    """文档标题：顶层 title；通用结构回退 conversations[0].title。"""
    title = str(data.get("title") or "").strip()
    if title:
        return title
    conversations = data.get("conversations")
    if isinstance(conversations, list) and conversations and isinstance(conversations[0], dict):
        return str(conversations[0].get("title") or "").strip()
    return ""


def doc_text(data: dict) -> str:
    """整份对话正文：标题一行 + 各轮按序拼接（供 chunk/索引）。"""
    parts: list[str] = []
    title = doc_title(data)
    if title:
        parts.append("# " + title)
    convs = data.get("convs")
    if isinstance(convs, list):
        for conv in convs:
            if not isinstance(conv, dict):
                continue
            t = conv_text(conv)
            if t:
                parts.append(t)
        return "\n\n".join(parts)
    for conv in data.get("conversations") or []:
        if not isinstance(conv, dict):
            continue
        t = _generic_conv_text(conv)
        if t:
            parts.append(t)
    return "\n\n".join(parts)