"""统一日志：控制台 + UTF-8 轮转文件。

节点/relay 都是长进程；日志默认写入用户配置目录下的 logs/，
单文件 10MB，保留 5 个备份。文件日志失败时退回控制台，不阻塞启动。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_MAX_BYTES = 10 * 1024 * 1024
_BACKUPS = 5
_FORMAT = "%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s"


def setup_logging(log_file: Optional[Path | str], level: str = "INFO") -> Optional[Path]:
    """安装 root handler；返回实际日志文件路径（失败为 None）。"""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    # 只清理本模块安装的 handler，避免影响调用方（测试/嵌入）。
    for handler in list(logger.handlers):
        if getattr(handler, "_chiselwall_handler", False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    formatter = logging.Formatter(_FORMAT)
    console: Optional[logging.Handler] = None
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console._chiselwall_handler = True  # type: ignore[attr-defined]
        logger.addHandler(console)

    path: Optional[Path] = None
    if log_file:
        try:
            path = Path(log_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                str(path), maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler._chiselwall_handler = True  # type: ignore[attr-defined]
            logger.addHandler(file_handler)
        except Exception as exc:
            path = None
            logger.warning("无法打开日志文件 %s：%s；仅输出控制台", log_file, exc)

    logging.captureWarnings(True)
    return path
