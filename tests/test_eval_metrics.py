"""R27：评测指标必须可复现，nDCG 使用理想排序归一化。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retrieval.metrics import binary_relevances, dcg, ndcg


class MetricsTest(unittest.TestCase):
    def test_dcg(self) -> None:
        self.assertAlmostEqual(dcg([1, 0, 1]), 1.5)

    def test_ndcg_ideal_is_one(self) -> None:
        self.assertAlmostEqual(ndcg([1, 1, 0]), 1.0)

    def test_ndcg_penalizes_bad_order(self) -> None:
        self.assertLess(ndcg([1, 0, 1]), 1.0)
        self.assertAlmostEqual(
            ndcg([1, 0, 1]),
            dcg([1, 0, 1]) / dcg([1, 1, 0]),
        )

    def test_empty_relevance(self) -> None:
        self.assertEqual(ndcg([0, 0, 0]), 0.0)

    def test_binary_relevances(self) -> None:
        self.assertEqual(binary_relevances([1, 2, 3], {2, 3}, 2), [0, 1])


if __name__ == "__main__":
    unittest.main()
