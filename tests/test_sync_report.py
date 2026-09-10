from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.sync_report import render_sync_report, render_terminal_summary


class SyncReportTests(unittest.TestCase):
    def test_report_names_mastery_uncheck_and_promotion(self) -> None:
        payload = {
            "status": "applied",
            "generated_at": "2026-09-10T10:00:00-04:00",
            "applied_at": "2026-09-10T10:02:00-04:00",
            "student": "Student A",
            "path_id": "test-path",
            "new_attempt_records": 1,
            "new_attempts": [
                {
                    "attempt_date": "2026-09-10",
                    "title": "Blend Sounds 2",
                    "variant": "Basic",
                    "score": 94,
                }
            ],
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
            "stretch_assignments": [],
            "observed_assignments": [{"title": "Blend Sounds 2", "variant": "Basic"}],
            "score_evidence": [
                {
                    "title": "Blend Sounds 2",
                    "variant": "Basic",
                    "scores": [85, 92, 94],
                    "status": "mastered",
                    "reason": "two consecutive attempts are at least 90%",
                },
                {
                    "title": "Blend Sounds 2",
                    "variant": "Main",
                    "scores": [],
                    "status": "not_mastered",
                    "reason": "no completed attempts",
                },
            ],
            "track_states": [],
            "performance": {"wall_seconds": 18.4},
        }

        report = render_sync_report(payload)

        self.assertIn("### Mastered", report)
        self.assertIn("### Applied unchecks", report)
        self.assertIn("### Applied promotions", report)
        self.assertIn("Blend Sounds 2 — Basic → Blend Sounds 2 — Main", report)
        self.assertIn("Scores: 85% → 92% → 94%", report)

        terminal = render_terminal_summary(payload)
        self.assertIn("Outcome: changes applied and final queue verified", terminal)
        self.assertIn("NEW SCORES", terminal)
        self.assertIn("MASTERY FOUND", terminal)
        self.assertIn("UNCHECKED", terminal)
        self.assertIn("ADDED", terminal)
        self.assertIn("Scores: 85% → 92% → 94%", terminal)
        self.assertIn(
            "Why: Next difficulty after Blend Sounds 2 — Basic met the mastery rule.",
            terminal,
        )
        self.assertIn("ASSIGNED NOW (1)", terminal)
        self.assertIn("Duration: 18.4 seconds", terminal)

    def test_review_output_labels_changes_as_not_applied(self) -> None:
        payload = {
            "status": "review_required",
            "generated_at": "2026-09-10T10:00:00-04:00",
            "student": "Student A",
            "new_attempt_records": 0,
            "actions": [
                {
                    "kind": "add",
                    "title": "Short Vowel Sound i",
                    "variant": "Basic",
                    "reason": "first unmastered activity in short_i_cvc_middle",
                }
            ],
            "observed_assignments": [],
            "desired_assignments": [{"title": "Short Vowel Sound i", "variant": "Basic"}],
            "stretch_assignments": [],
            "score_evidence": [
                {
                    "title": "Short Vowel Sound i",
                    "variant": "Basic",
                    "scores": [],
                    "status": "not_mastered",
                    "reason": "no completed attempts",
                }
            ],
        }

        terminal = render_terminal_summary(payload)

        self.assertIn("changes proposed but not applied", terminal)
        self.assertIn("WILL BE ADDED (NOT YET APPLIED)", terminal)
        self.assertIn("short i cvc middle", terminal)

    def test_no_op_output_is_explicit(self) -> None:
        payload = {
            "status": "no_op",
            "student": "Student A",
            "new_attempt_records": 0,
            "new_attempts": [],
            "actions": [],
            "observed_assignments": [{"title": "Blend Sounds 2", "variant": "Main"}],
            "desired_assignments": [{"title": "Blend Sounds 2", "variant": "Main"}],
            "stretch_assignments": [],
            "score_evidence": [
                {
                    "title": "Blend Sounds 2",
                    "variant": "Main",
                    "scores": [69],
                    "status": "not_mastered",
                    "reason": "latest attempt is below 90% (69%)",
                }
            ],
        }

        terminal = render_terminal_summary(payload)

        self.assertIn("no changes needed; live queue verified", terminal)
        self.assertIn("NEW SCORES\n  None.", terminal)
        self.assertIn("UNCHECKED\n  None.", terminal)
        self.assertIn("ADDED\n  None.", terminal)
        self.assertIn("HOLD; scores: 69%", terminal)

    def test_interrupted_terminal_output_shows_only_completed_actions(self) -> None:
        payload = {
            "status": "interrupted",
            "student": "Student A",
            "new_attempt_records": 0,
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
            "observed_assignments": [],
            "desired_assignments": [],
            "stretch_assignments": [],
            "score_evidence": [],
        }

        terminal = render_terminal_summary(payload)

        self.assertIn("interrupted; only completed actions are shown", terminal)
        self.assertIn("ADDED BEFORE INTERRUPTION", terminal)
        self.assertIn("Short Vowel Sound i", terminal)
        self.assertNotIn("Short Vowel Sound e", terminal)

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
