from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.sync_report import (
    recommend_next_lessons,
    render_sync_report,
    render_terminal_summary,
    terminal_color_enabled,
)


class SyncReportTests(unittest.TestCase):
    def test_do_next_ranks_near_mastery_then_strong_score_then_variety(self) -> None:
        lessons = ["Blend", "Words", "New", "Sounds", "Provisional", "Mastered"]
        scores = [[78], [88], [], [78], [96], [100]]
        payload = {
            "student": "Student A",
            "status": "no_op",
            "generated_at": "2026-09-16",
            "desired_assignments": [{"title": title, "variant": "Main"} for title in lessons],
            "score_evidence": [
                {"title": title, "variant": "Main", "scores": attempts}
                for title, attempts in zip(lessons, scores, strict=True)
            ],
        }
        original = repr(payload)
        recommendations = recommend_next_lessons(payload)
        self.assertEqual(
            [item["title"] for item in recommendations], ["Provisional", "Words", "New"]
        )
        self.assertIn("Another 90%+", recommendations[0]["mastery_goal"])
        self.assertIn("100%", recommendations[1]["mastery_goal"])
        self.assertEqual(recommendations, recommend_next_lessons(payload))
        self.assertEqual(repr(payload), original)
        for render in (render_sync_report, render_terminal_summary):
            report = render(payload)
            self.assertIn("ADVISORY", report.upper())
            self.assertIn("1. Provisional", report)

    def test_do_next_excludes_unverified_and_missing_or_durable_mastery_evidence(self) -> None:
        payload = {
            "status": "review_required",
            "desired_assignments": [{"title": "Lesson", "variant": "Main"}],
            "score_evidence": [{"title": "Lesson", "variant": "Main", "scores": [96]}],
        }
        for status in ("review_required", "interrupted", "applying"):
            payload["status"] = status
            self.assertEqual(recommend_next_lessons(payload), [])
        payload["status"] = "no_op"
        payload["score_evidence"][0]["status"] = "mastered"
        self.assertEqual(recommend_next_lessons(payload), [])
        payload["score_evidence"] = []
        self.assertEqual(recommend_next_lessons(payload), [])

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
            "score_scan_mode": "live_all_available",
            "score_controls_read": [
                {
                    "title": "Blend Sounds 2",
                    "variant": "Basic",
                    "attempts_newest_first": [
                        {"attempt_date": "2026-09-10", "score": 94},
                        {"attempt_date": "2026-09-09", "score": 92},
                    ],
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
            "active_quarantines": [
                {
                    "title": "Words with b, c, d",
                    "active_through": "2026-10-10",
                    "eligible_date": "2026-10-11",
                    "reason": "latest score was 39%",
                }
            ],
            "performance": {"wall_seconds": 18.4},
        }

        report = render_sync_report(payload)

        self.assertIn("### Mastered", report)
        self.assertIn("### Applied unchecks", report)
        self.assertIn("### Applied promotions", report)
        self.assertIn("Blend Sounds 2 — Basic → Blend Sounds 2 — Main", report)
        self.assertIn("Scores: 85% → 92% → 94%", report)
        self.assertIn("### Active quarantines", report)
        self.assertIn("eligible again 2026-10-11", report)
        self.assertIn("### Score controls read", report)
        self.assertIn("94% on 2026-09-10, 92% on 2026-09-09", report)

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
        self.assertIn("ACTIVE QUARANTINES", terminal)
        self.assertIn("Words with b, c, d", terminal)
        self.assertIn("LIVE SCORE CONTROLS OPENED (1)", terminal)

        colored = render_terminal_summary(payload, color=True)
        self.assertIn("\033[32mMASTERY FOUND\033[0m", colored)
        self.assertIn("\033[34mADDED\033[0m", colored)
        self.assertIn("\033[33mNEW SCORES\033[0m", colored)

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
        self.assertIn("CHANGES SINCE LAST SYNC", terminal)
        self.assertIn("No new lesson attempts or assignment changes", terminal)
        self.assertIn("NEW SCORES\n  ○ None.", terminal)
        self.assertIn("UNCHECKED\n  None.", terminal)
        self.assertIn("ADDED\n  None.", terminal)
        self.assertIn("HOLD; scores: 69%", terminal)

    def test_interrupted_terminal_output_shows_only_completed_actions(self) -> None:
        payload = _interrupted_payload()

        terminal = render_terminal_summary(payload)

        self.assertIn("interrupted; only completed actions are shown", terminal)
        self.assertIn("ADDED BEFORE INTERRUPTION", terminal)
        self.assertIn("Short Vowel Sound i", terminal)
        self.assertNotIn("Short Vowel Sound e", terminal)

    def test_terminal_color_auto_respects_tty_no_color_and_dumb_term(self) -> None:
        stream = Mock()
        stream.isatty.return_value = True

        with patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True):
            self.assertTrue(terminal_color_enabled("auto", stream))
        with patch.dict("os.environ", {"TERM": "xterm-256color", "NO_COLOR": "1"}, clear=True):
            self.assertFalse(terminal_color_enabled("auto", stream))
            self.assertTrue(terminal_color_enabled("always", stream))
        with patch.dict("os.environ", {"TERM": "dumb"}, clear=True):
            self.assertFalse(terminal_color_enabled("auto", stream))
        self.assertFalse(terminal_color_enabled("never", stream))

    def test_interrupted_report_includes_only_completed_actions(self) -> None:
        payload = _interrupted_payload()

        report = render_sync_report(payload)

        self.assertIn("Applied before interruption additions", report)
        self.assertIn("Short Vowel Sound i", report)
        self.assertNotIn("Short Vowel Sound e", report)


def _interrupted_payload() -> dict[str, object]:
    return {
        "status": "interrupted",
        "generated_at": "2026-09-10T10:00:00-04:00",
        "interrupted_at": "2026-09-10T10:01:00-04:00",
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
        "track_states": [],
    }


if __name__ == "__main__":
    unittest.main()
