"""资源保护（设计 §12、调研 02 §5.2）：进程低优先级 + 空闲调度（红线③）。

    - lower_process_priority()：Windows 设 BELOW_NORMAL_PRIORITY_CLASS（psutil），
      POSIX 用 nice(10)——索引进程尽量不与用户前台程序抢 CPU；
    - current_cpu_load() / wait_if_busy()：空闲调度——CPU 负载高于阈值时暂停推进
      索引队列；采样限频 1Hz 避免采样本身吃 CPU；单批最多等待 30s 防饿死；
    - yield_to_user()：单文件处理之间让出，保证用户操作不卡顿。

达标线（评审结论①）：索引期 CPU ≤ 50%、峰值内存 ≤ 1GB（4C8G 参考机）。
"""
from __future__ import annotations

import os
import time

try:
    import psutil
except ImportError:  # 依赖缺失时降级为无操作（功能可用性优先，不阻断索引）
    psutil = None  # type: ignore[assignment]

# 空闲调度参数（评审结论①：索引期 CPU ≤ 50% -> 默认阈值 70 已留裕量，可配）
CPU_THRESHOLD_DEFAULT: float = 70.0
_SAMPLE_INTERVAL: float = 0.2   # 单次采样窗口（秒）
_SAMPLE_MIN_GAP: float = 1.0    # 采样限频（秒）
_WAIT_STEP: float = 2.0
_MAX_TOTAL_WAIT: float = 30.0

_last_sample_at: float = 0.0
_last_load: float = 0.0


def lower_process_priority() -> None:
    """把当前进程降到低优先级。失败静默（权限/平台差异不阻断索引）。"""
    if psutil is None:
        return
    try:
        p = psutil.Process()
        if os.name == "nt":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except Exception:
        pass


def current_cpu_load() -> float:
    """当前 CPU 总负载（%）。采样限频 1Hz；首次调用返回 0（视为空闲）。"""
    global _last_sample_at, _last_load
    now = time.monotonic()
    if now - _last_sample_at >= _SAMPLE_MIN_GAP:
        try:
            if psutil is not None:
                _last_load = float(psutil.cpu_percent(interval=_SAMPLE_INTERVAL))
        except Exception:
            _last_load = 0.0
        _last_sample_at = now
    return _last_load


def wait_if_busy(
    threshold: float = CPU_THRESHOLD_DEFAULT,
    max_total_wait: float = _MAX_TOTAL_WAIT,
) -> bool:
    """空闲调度：CPU 负载 > 阈值时暂停推进；返回是否等待过。"""
    waited = 0.0
    while current_cpu_load() > threshold and waited < max_total_wait:
        time.sleep(_WAIT_STEP)
        waited += _WAIT_STEP
    return waited > 0


def yield_to_user(seconds: float = 0.05) -> None:
    """单文件处理之间让出 CPU（用户前台操作不卡顿）。"""
    time.sleep(seconds)
