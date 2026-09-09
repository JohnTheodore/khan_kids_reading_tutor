from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.catalog import CatalogIndex
from khan_kids.reports import ScoreAttempt, ScoreHistory
from mastery_workflow import build_plan


class WorkflowTests(unittest.TestCase):
    def test_build_plan_promotes_a_perfect_latest_attempt(self) -> None:
        history = ScoreHistory(
            student="Student A",
            title="Blend Sounds 2",
            variant="Basic",
            curriculum_path=(
                "A4: ELA: Reading Foundational Skills: Phonological Awareness: Three-Phoneme Words"
            ),
            assigned_date=date(2026, 9, 8),
            attempts_newest_first=(
                ScoreAttempt("Today", date(2026, 9, 9), 100),
                ScoreAttempt("Yesterday", date(2026, 9, 8), 70),
            ),
        )

        attempts, decisions = build_plan(
            (history,), CatalogIndex(Path("data/reading-ela-archive.json"))
        )

        self.assertEqual([item["score_percent"] for item in attempts], [70, 100])
        self.assertEqual(decisions[0]["status"], "mastered")
        self.assertEqual(decisions[0]["action"], "promote")
        self.assertEqual(decisions[0]["next_variant"], "Main")


if __name__ == "__main__":
    unittest.main()
