from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.records import (
    append_unique_rows,
    append_unique_rows_with_records,
    read_mastered_action_keys,
    record_action,
)


class RecordTests(unittest.TestCase):
    def test_append_is_duplicate_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.csv"
            fields = ("student", "lesson", "score")
            row = {"student": "Student A", "lesson": "Blend Sounds", "score": 100}
            self.assertEqual(append_unique_rows(path, fields, [row], identity_fields=fields), 1)
            self.assertEqual(append_unique_rows(path, fields, [row], identity_fields=fields), 0)
            with path.open(newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            self.assertNotIn(b"\r\n", path.read_bytes())

    def test_append_can_return_the_exact_new_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.csv"
            fields = ("student", "lesson", "score")
            first = {"student": "Student A", "lesson": "Blend Sounds", "score": 92}
            second = {"student": "Student A", "lesson": "Blend Sounds", "score": 94}
            append_unique_rows(path, fields, [first], identity_fields=fields)

            additions = append_unique_rows_with_records(
                path, fields, [first, second], identity_fields=fields
            )

            self.assertEqual(
                additions,
                [{"student": "Student A", "lesson": "Blend Sounds", "score": "94"}],
            )

    def test_occurrence_reconciliation_preserves_identical_attempts_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.csv"
            fields = ("student", "lesson", "score")
            row = {"student": "Student A", "lesson": "Beginning Sounds", "score": 94}
            append_unique_rows(path, fields, [row], identity_fields=fields)

            first = append_unique_rows_with_records(
                path,
                fields,
                [row, row],
                identity_fields=fields,
                reconcile_occurrences=True,
            )
            second = append_unique_rows_with_records(
                path,
                fields,
                [row, row],
                identity_fields=fields,
                reconcile_occurrences=True,
            )

            self.assertEqual(first, [{key: str(value) for key, value in row.items()}])
            self.assertEqual(second, [])
            with path.open(newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)

    def test_mastered_actions_are_monotonic_and_each_event_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "actions.csv"
            details = {
                "action_date": date(2026, 9, 13),
                "student": "Student A",
                "action": "unchecked",
                "title": "Beginning Sounds 2",
                "variant": "Basic",
                "reason": "mastered: two qualifying attempts",
                "result": "saved",
            }
            record_action(path, **details, recorded_at=datetime(2026, 9, 13, 8, 0, 0))
            record_action(path, **details, recorded_at=datetime(2026, 9, 13, 8, 1, 0))

            self.assertEqual(
                read_mastered_action_keys(path, "Student A"),
                {("Beginning Sounds 2", "Basic")},
            )
            with path.open(newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)


if __name__ == "__main__":
    unittest.main()
