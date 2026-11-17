"""ONNX Runtime 可用性探测。

ONNX DLL 损坏/ABI 不兼容时，直接 import 会触发进程级 access violation，
Python 的 try/except 捕获不到。这里用子进程探测，主进程绝不导入 ONNX；
探测失败时上层回退 BM25。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from functools import lru_cache

log = logging.getLogger("chiselwall.onnx")


@lru_cache(maxsize=1)
def probe_onnx(timeout_s: float = 10.0) -> tuple[bool, str]:
    if os.environ.get("CHISELWALL_DISABLE_ONNX") == "1":
        return False, "CHISELWALL_DISABLE_ONNX=1"
    if getattr(sys, "frozen", False):
        # PyInstaller 的 sys.executable 不是 Python；默认信任打包内 ORT。
        return True, "frozen"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import onnxruntime; print(onnxruntime.__version__)"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except Exception as exc:
        return False, "ONNX 探测失败: " + (str(exc) or exc.__class__.__name__)
    if proc.returncode != 0:
        return False, "ONNX Runtime 不可用（exit=%s）" % proc.returncode
    version = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    log.info("ONNX Runtime 可用 version=%s", version)
    return True, version


def available() -> bool:
    return probe_onnx()[0]
