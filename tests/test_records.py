from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.records import append_unique_rows, append_unique_rows_with_records


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


if __name__ == "__main__":
    unittest.main()
