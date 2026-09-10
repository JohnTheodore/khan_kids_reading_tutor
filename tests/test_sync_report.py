from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.sync_report import render_sync_report


class SyncReportTests(unittest.TestCase):
    def test_report_names_mastery_uncheck_and_promotion(self) -> None:
        payload = {
            "status": "applied",
            "generated_at": "2026-09-10T10:00:00-04:00",
            "applied_at": "2026-09-10T10:02:00-04:00",
            "student": "Student A",
            "path_id": "test-path",
            "new_attempt_records": 1,
            "actions": [
                {
                    "kind": "remove",
                    "title": "Blend Sounds 2",
                    "variant": "Basic",
                    "reason": (
                        "mastered: two consecutive attempts are at least 90%; "
                        "promote to Blend Sounds 2 — Main"
                    ),
                },
                {
                    "kind": "add",
                    "title": "Blend Sounds 2",
                    "variant": "Main",
                    "reason": "next activity",
                },
            ],
            "desired_assignments": [{"title": "Blend Sounds 2", "variant": "Main"}],
            "track_states": [],
        }

        report = render_sync_report(payload)

        self.assertIn("### Mastered", report)
        self.assertIn("### Applied unchecks", report)
        self.assertIn("### Applied promotions", report)
        self.assertIn("Blend Sounds 2 — Basic → Blend Sounds 2 — Main", report)

    def test_interrupted_report_includes_only_completed_actions(self) -> None:
        payload = {
            "status": "interrupted",
            "generated_at": "2026-09-10T10:00:00-04:00",
            "interrupted_at": "2026-09-10T10:01:00-04:00",
            "student": "Student A",
            "actions": [
                {
                    "kind": "add",
                    "title": "Short Vowel Sound i",
                    "variant": "Basic",
                    "reason": "first unmastered activity",
                },
                {
                    "kind": "add",
                    "title": "Short Vowel Sound e",
                    "variant": "Basic",
                    "reason": "first unmastered activity",
                },
            ],
            "applied": [
                {
                    "action": "checked",
                    "title": "Short Vowel Sound i",
                    "variant": "Basic",
                }
            ],
            "desired_assignments": [],
            "track_states": [],
        }

        report = render_sync_report(payload)

        self.assertIn("Applied before interruption additions", report)
        self.assertIn("Short Vowel Sound i", report)
        self.assertNotIn("Short Vowel Sound e", report)


if __name__ == "__main__":
    unittest.main()
