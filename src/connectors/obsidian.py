"""ObsidianConnector（设计 §6、调研 02 §3.2）。

vault 即普通本地目录，笔记就是 Markdown 纯文本文件（含子目录），
复用 DirectoryConnector 的全部解析能力；.obsidian 配置目录在默认排除名单中
（ExclusionEngine.BUILTIN_DIRS），vault 内其余 .md 正常索引。
"""
from __future__ import annotations

from .directory import DirectoryConnector


class ObsidianConnector(DirectoryConnector):
    """Obsidian 库连接器：零迁移，直接以 vault 目录作为挂载点。"""

    kind = "obsidian"

    def default_excludes(self) -> list[str]:
        """Obsidian 配置目录不参与索引（另有内置规则兜底）。"""
        return [".obsidian/**"]
