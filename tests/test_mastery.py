from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.mastery import MasteryStatus, evaluate_mastery, next_variant


class MasteryTests(unittest.TestCase):
    def test_perfect_latest_attempt_is_mastered(self) -> None:
        self.assertEqual(evaluate_mastery([70, 100]).status, MasteryStatus.MASTERED)

    def test_two_consecutive_nineties_are_mastered(self) -> None:
        self.assertEqual(evaluate_mastery([85, 92, 90]).status, MasteryStatus.MASTERED)

    def test_one_ninety_is_provisional(self) -> None:
        self.assertEqual(evaluate_mastery([85, 92]).status, MasteryStatus.PROVISIONAL)

    def test_latest_regression_prevents_promotion(self) -> None:
        self.assertEqual(evaluate_mastery([90, 80]).status, MasteryStatus.NOT_MASTERED)

    def test_next_variant_skips_unavailable_rungs(self) -> None:
        self.assertEqual(next_variant("Basic", ("Basic", "Practice 1")), "Practice 1")
        self.assertIsNone(next_variant("Practice 2", ("Basic", "Practice 2")))


if __name__ == "__main__":
    unittest.main()
