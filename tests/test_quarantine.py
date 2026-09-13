from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.quarantine import (
    LessonQuarantine,
    append_low_score_quarantines,
    read_active_quarantines,
)


class QuarantineTests(unittest.TestCase):
    def _evaluate_low_score(
        self, scores: tuple[int, ...], *, has_new_attempt: bool
    ) -> tuple[LessonQuarantine, ...]:
        attempt = {
            "lesson_title": "Words: End Sound",
            "activity_variant": "Main",
        }
        with tempfile.TemporaryDirectory() as temporary:
            return append_low_score_quarantines(
                Path(temporary) / "quarantines.csv",
                student="Student A",
                today=date(2026, 9, 12),
                scores={("Words: End Sound", "Main"): scores},
                new_attempts=(attempt,) if has_new_attempt else (),
            )

    def test_record_is_active_until_but_not_on_eligible_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "quarantines.csv"
            path.write_text(
                "student,title,start_date,eligible_date,reason\n"
                'Student A,"Words with b, c, d",2026-09-11,2026-10-11,low score\n'
            )

            active = read_active_quarantines(path, student="Student A", today=date(2026, 10, 10))
            expired = read_active_quarantines(path, student="Student A", today=date(2026, 10, 11))

        self.assertEqual(active[0].active_through, date(2026, 10, 10))
        self.assertEqual(expired, ())

    def test_invalid_interval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "quarantines.csv"
            path.write_text(
                "student,title,start_date,eligible_date,reason\n"
                "Student A,Lesson,2026-09-11,2026-09-11,invalid\n"
            )

            with self.assertRaisesRegex(ValueError, "must follow"):
                read_active_quarantines(path, student="Student A", today=date(2026, 9, 11))

    def test_fourth_attempt_below_70_starts_a_fourteen_day_quarantine(self) -> None:
        active = self._evaluate_low_score((83, 56, 68, 69), has_new_attempt=True)

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].title, "Words: End Sound")
        self.assertEqual(active[0].eligible_date, date(2026, 9, 26))
        self.assertIn("83% → 56% → 68% → 69%", active[0].reason)

    def test_quarantine_requires_a_new_attempt_and_does_not_renew_from_old_history(self) -> None:
        active = self._evaluate_low_score((83, 56, 68, 69), has_new_attempt=False)
        self.assertEqual(active, ())

    def test_latest_score_of_70_does_not_trigger_quarantine(self) -> None:
        active = self._evaluate_low_score((83, 56, 68, 70), has_new_attempt=True)
        self.assertEqual(active, ())


if __name__ == "__main__":
    unittest.main()
