from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import nullcontext, redirect_stdout
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AutomationError
from khan_kids.automation import ActionResult
from khan_kids.catalog import CatalogIndex
from khan_kids.curriculum import Activity, ReadingCurriculum
from khan_kids.planner import QueueAction, QueuePlan, build_queue_plan
from khan_kids.records import read_attempt_scores
from khan_kids.reports import AssignmentRow, AssignmentSnapshot, ScoreAttempt, ScoreHistory
from khan_kids.ui import Rect
from khan_kids.workflow import histories_to_attempt_rows
from reading_workflow import _apply_reviewed_plan, create_plan_payload, validate_reviewed_plan

CATALOG_PATH = Path("data/reading-ela-archive.json")
CURRICULUM_PATH = Path("data/reading-curriculum.json")
EXPECTED_LIVE_QUEUE = {
    ("Blend Sounds 2", "Main"),
    ("Make New Words", "Basic"),
    ("Words: End Sound", "Main"),
    ("Short Vowel Sound a", "Main"),
    ("Short Vowel Sound i", "Basic"),
    ("Short Vowel Sound u", "Main"),
    ("Words with b, c, d", "Main"),
    ("Words with f, g, h", "Main"),
    ("Words with m & n", "Main"),
    ("Words with b & d", "Main"),
}
STRETCH_QUEUE = {
    ("Words with f, g, h", "Main"),
    ("Words with m & n", "Main"),
    ("Words with b & d", "Main"),
}
EXPECTED_DIVERSE_QUEUE = EXPECTED_LIVE_QUEUE - STRETCH_QUEUE
EXPECTED_TEN_QUEUE = EXPECTED_LIVE_QUEUE


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CatalogIndex(CATALOG_PATH)
        cls.curriculum = ReadingCurriculum.load(CURRICULUM_PATH, cls.catalog)

    def test_current_records_preserve_the_diverse_ten_item_queue(self) -> None:
        scores = read_attempt_scores(Path("student-records/student-a-lesson-attempts.csv"), "Student A")

        plan = build_queue_plan(self.curriculum, scores, EXPECTED_LIVE_QUEUE)

        self.assertEqual({activity.key for activity in plan.desired}, EXPECTED_TEN_QUEUE)
        self.assertEqual(plan.actions, ())

    def test_unattempted_stretch_is_pinned_and_low_score_rotates_without_forgetting(self) -> None:
        scores = read_attempt_scores(Path("student-records/student-a-lesson-attempts.csv"), "Student A")
        current = set(EXPECTED_TEN_QUEUE)

        untouched = build_queue_plan(self.curriculum, scores, current)
        self.assertEqual({activity.key for activity in untouched.desired}, current)

        scores[("Words with f, g, h", "Main")] = (65,)
        rotated = build_queue_plan(self.curriculum, scores, current)
        desired = {activity.key for activity in rotated.desired}
        self.assertNotIn(("Words with f, g, h", "Main"), desired)
        self.assertIn(("Words with m, n, p", "Main"), desired)
        removal = next(
            action for action in rotated.actions if action.key == ("Words with f, g, h", "Main")
        )
        self.assertIn("deferred for retry", removal.reason)

    def test_deferred_stretch_becomes_eligible_after_supporting_mastery(self) -> None:
        scores = read_attempt_scores(Path("student-records/student-a-lesson-attempts.csv"), "Student A")
        scores[("Words with f, g, h", "Main")] = (65,)
        blend_track = next(
            track for track in self.curriculum.tracks if track.track_id == "three_phoneme_blending"
        )
        for activity in blend_track.activities:
            scores[activity.key] = (100,)

        plan = build_queue_plan(self.curriculum, scores, EXPECTED_DIVERSE_QUEUE)

        self.assertIn(("Words with f, g, h", "Main"), {activity.key for activity in plan.desired})

    def test_completed_vowel_track_rotates_in_next_deferred_vowel(self) -> None:
        scores = read_attempt_scores(Path("student-records/student-a-lesson-attempts.csv"), "Student A")
        short_a = next(
            track for track in self.curriculum.tracks if track.track_id == "short_a_cvc_middle"
        )
        for activity in short_a.activities:
            scores[activity.key] = (100,)

        plan = build_queue_plan(self.curriculum, scores, EXPECTED_DIVERSE_QUEUE)
        desired = {activity.key for activity in plan.desired}

        self.assertNotIn(("Short Vowel Sound a", "Basic"), desired)
        self.assertIn(("Short Vowel Sound e", "Basic"), desired)
        vowel_titles = {title for title, _variant in desired if title.startswith("Short Vowel")}
        self.assertEqual(
            vowel_titles,
            {"Short Vowel Sound i", "Short Vowel Sound e", "Short Vowel Sound u"},
        )

    def test_curriculum_grade_disambiguates_repeated_live_title(self) -> None:
        history = ScoreHistory(
            student="Student A",
            title="Short Vowel Sound a",
            variant="Basic",
            curriculum_path="",
            assigned_date=date(2026, 9, 9),
            attempts_newest_first=(ScoreAttempt("Today", date(2026, 9, 10), 100),),
        )

        rows = histories_to_attempt_rows(
            (history,),
            self.catalog,
            preferred_grades={(history.title, history.variant): "Preschool (Age 4)"},
        )

        self.assertEqual(rows[0]["report_grade"], "Preschool (Age 4)")

    def test_mastery_replaces_a_rung_without_growing_the_queue(self) -> None:
        current = set(EXPECTED_TEN_QUEUE)
        current.remove(("Blend Sounds 2", "Main"))
        current.add(("Blend Sounds 2", "Basic"))
        scores = {
            ("Blend Sounds 2", "Basic"): (85, 92, 90),
            ("Make New Words", "Basic"): (92,),
            ("Words: End Sound", "Basic"): (100,),
            ("Words: End Sound", "Main"): (83,),
            ("Blend Syllables", "Basic"): (100,),
            ("Blend Syllables", "Main"): (100,),
            ("Blend Syllables", "Practice 1"): (100,),
            ("Blend Syllables", "Practice 2"): (100,),
            ("Short Vowel Sound a", "Basic"): (100,),
            ("Short Vowel Sound u", "Basic"): (100,),
        }

        plan = build_queue_plan(self.curriculum, scores, current)

        self.assertEqual(len(plan.desired), 10)
        self.assertIn(("Blend Sounds 2", "Main"), {activity.key for activity in plan.desired})
        self.assertEqual(
            [(action.kind, action.key) for action in plan.actions],
            [
                ("remove", ("Blend Sounds 2", "Basic")),
                ("add", ("Blend Sounds 2", "Main")),
            ],
        )

    def test_reviewed_plan_is_rejected_when_live_state_changes(self) -> None:
        snapshot = _snapshot(score=92)
        current = {(row.title, row.variant) for row in snapshot.rows}
        plan = build_queue_plan(self.curriculum, {}, current)
        payload = create_plan_payload(
            student="Student A",
            snapshot=snapshot,
            plan=plan,
            curriculum=self.curriculum,
            catalog_path=CATALOG_PATH,
            curriculum_path=CURRICULUM_PATH,
            new_attempt_records=0,
            generated_at=datetime(2026, 9, 9),
        )
        self.assertEqual(payload["path_id"], "minimum-viable-reading-path-v1")

        with self.assertRaisesRegex(AutomationError, "stale"):
            validate_reviewed_plan(
                payload,
                student="Student A",
                snapshot=_snapshot(score=100),
                catalog_path=CATALOG_PATH,
                curriculum_path=CURRICULUM_PATH,
                curriculum=self.curriculum,
            )

    def test_reviewed_plan_accepts_catalog_validated_stretch_activities(self) -> None:
        snapshot = AssignmentSnapshot((), ())
        scores = read_attempt_scores(Path("student-records/student-a-lesson-attempts.csv"), "Student A")
        plan = build_queue_plan(self.curriculum, scores, set())
        payload = create_plan_payload(
            student="Student A",
            snapshot=snapshot,
            plan=plan,
            curriculum=self.curriculum,
            catalog_path=CATALOG_PATH,
            curriculum_path=CURRICULUM_PATH,
            new_attempt_records=0,
            generated_at=datetime(2026, 9, 10),
        )

        desired, _actions = validate_reviewed_plan(
            payload,
            student="Student A",
            snapshot=snapshot,
            catalog_path=CATALOG_PATH,
            curriculum_path=CURRICULUM_PATH,
            curriculum=self.curriculum,
        )

        self.assertIn(("Words with f, g, h", "Main"), {activity.key for activity in desired})

    def test_apply_records_each_action_and_verifies_the_final_queue(self) -> None:
        snapshot = _snapshot(score=92)
        desired = Activity("Preschool (Age 4)", "Blend Sounds 2", "Main")
        plan = QueuePlan(
            (desired,),
            (
                QueueAction("remove", "Blend Sounds 2", "Basic", "", "Basic mastered"),
                QueueAction(
                    "add",
                    "Blend Sounds 2",
                    "Main",
                    "Preschool (Age 4)",
                    "next rung",
                ),
            ),
            (),
        )
        payload = create_plan_payload(
            student="Student A",
            snapshot=snapshot,
            plan=plan,
            curriculum=self.curriculum,
            catalog_path=CATALOG_PATH,
            curriculum_path=CURRICULUM_PATH,
            new_attempt_records=1,
            generated_at=datetime(2026, 9, 9),
            scores={("Blend Sounds 2", "Basic"): (85, 92, 94)},
            new_attempts=[
                {
                    "attempt_date": "2026-09-09",
                    "lesson_title": "Blend Sounds 2",
                    "activity_variant": "Basic",
                    "score_percent": "94",
                }
            ],
        )
        self.assertEqual(payload["new_attempts"][0]["score"], 94)
        self.assertEqual(payload["score_evidence"][0]["scores"], [85, 92, 94])
        final_snapshot = AssignmentSnapshot(
            (
                AssignmentRow(
                    title="Blend Sounds 2",
                    variant="Main",
                    assigned_date="Today",
                    rect=Rect(0, 0, 100, 100),
                    score=None,
                    score_rect=None,
                ),
            ),
            (),
        )
        automation = Mock()
        automation.device.timing.span.return_value = nullcontext()
        automation.unassign_many.return_value = iter(
            (ActionResult("unchecked", "Blend Sounds 2", "Basic", "saved"),)
        )
        automation.assign_many.return_value = iter(
            (ActionResult("checked", "Blend Sounds 2", "Main", "saved"),)
        )
        automation.scan_assignments.return_value = final_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            args = Namespace(
                student="Student A",
                catalog=CATALOG_PATH,
                curriculum=CURRICULUM_PATH,
                max_actions=2,
                today=date(2026, 9, 9),
                apply_plan=temporary_path / "plan.json",
            )
            with redirect_stdout(StringIO()):
                _apply_reviewed_plan(
                    args=args,
                    payload=payload,
                    snapshot=snapshot,
                    automation=automation,
                    actions_path=temporary_path / "actions.csv",
                    report_path=temporary_path / "sync-log.md",
                    curriculum=self.curriculum,
                    catalog=self.catalog,
                )

            self.assertEqual(payload["status"], "applied")
            self.assertEqual(len((temporary_path / "actions.csv").read_text().splitlines()), 3)
            self.assertTrue(args.apply_plan.exists())
            self.assertIn("Applied promotions", (temporary_path / "sync-log.md").read_text())


def _snapshot(*, score: int) -> AssignmentSnapshot:
    row = AssignmentRow(
        title="Blend Sounds 2",
        variant="Basic",
        assigned_date="Today",
        rect=Rect(0, 0, 100, 100),
        score=score,
        score_rect=Rect(100, 0, 200, 100),
    )
    return AssignmentSnapshot((row,), ())


if __name__ == "__main__":
    unittest.main()
