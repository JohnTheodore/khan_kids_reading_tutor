from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.quarantine import read_active_quarantines


class QuarantineTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
