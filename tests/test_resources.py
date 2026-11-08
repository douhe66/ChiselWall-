"""资源控制单测（设计 §12、红线③）：低优先级、CPU 采样限频、空闲调度。

运行：python -m unittest tests.test_resources
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import indexer.resources as res


class FakePsutil:
    """替代 psutil：可控 CPU 负载与调用计数。"""

    def __init__(self) -> None:
        self.load = 0.0
        self.calls = 0

    def cpu_percent(self, interval=None):
        self.calls += 1
        return self.load


class ResourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = res.psutil
        res._last_sample_at = 0.0
        res._last_load = 0.0

    def tearDown(self) -> None:
        res.psutil = self._orig
        res._last_sample_at = 0.0
        res._last_load = 0.0

    def test_lower_priority_no_exception(self) -> None:
        res.psutil = FakePsutil()  # type: ignore[assignment]
        res.lower_process_priority()  # 不抛异常（真实环境下同样静默）
        res.psutil = None
        res.lower_process_priority()  # 依赖缺失时降级

    def test_cpu_load_sample_rate_limited(self) -> None:
        fake = FakePsutil()
        fake.load = 30.0
        res.psutil = fake  # type: ignore[assignment]
        self.assertEqual(res.current_cpu_load(), 30.0)
        self.assertEqual(res.current_cpu_load(), 30.0)  # 1s 内不再采样
        self.assertEqual(fake.calls, 1)

    def test_wait_if_busy_waits_then_proceeds(self) -> None:
        fake = FakePsutil()
        fake.load = 90.0
        res.psutil = fake  # type: ignore[assignment]
        start = time.monotonic()
        waited = res.wait_if_busy(threshold=70.0, max_total_wait=3.0)
        self.assertTrue(waited)
        self.assertGreaterEqual(time.monotonic() - start, 2.0)
        self.assertLess(time.monotonic() - start, 6.0)

    def test_wait_if_busy_idle_no_wait(self) -> None:
        fake = FakePsutil()
        fake.load = 10.0
        res.psutil = fake  # type: ignore[assignment]
        self.assertFalse(res.wait_if_busy(threshold=70.0, max_total_wait=3.0))


if __name__ == "__main__":
    unittest.main()
