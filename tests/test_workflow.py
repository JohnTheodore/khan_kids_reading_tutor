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
from khan_kids.quarantine import LessonQuarantine
from khan_kids.records import read_attempt_scores
from khan_kids.reports import AssignmentRow, AssignmentSnapshot, ScoreAttempt, ScoreHistory
from khan_kids.ui import Rect
from khan_kids.workflow import histories_to_attempt_rows
from reading_workflow import (
    _apply_reviewed_plan,
    _history_lookup_for_run,
    create_plan_payload,
    validate_reviewed_plan,
)

CATALOG_PATH = Path("data/reading-ela-archive.json")
CURRICULUM_PATH = Path("data/reading-curriculum.json")
PRE_QUARANTINE_QUEUE = {
    ("Blend Sounds 2", "Practice 1"),
    ("Make New Words", "Main"),
    ("Words: End Sound", "Main"),
    ("Short Vowel Sound a", "Main"),
    ("Short Vowel Sound i", "Main"),
    ("Short Vowel Sound u", "Main"),
    ("Words with b, c, d", "Main"),
    ("Words with f, g, h", "Main"),
    ("Words with m & n", "Main"),
    ("Words with b & d", "Main"),
}
BASELINE_STRETCH_QUEUE = {
    ("Words with f, g, h", "Main"),
    ("Words with m & n", "Main"),
    ("Words with b & d", "Main"),
}
EXPECTED_DIVERSE_QUEUE = PRE_QUARANTINE_QUEUE - BASELINE_STRETCH_QUEUE
FIRST_QUARANTINE_QUEUE = PRE_QUARANTINE_QUEUE - {("Words with b, c, d", "Main")} | {
    ("Words with m, n, p", "Main")
}
EXPECTED_BEGINNING_QUARANTINE_QUEUE = PRE_QUARANTINE_QUEUE - {
    ("Words with b, c, d", "Main"),
    ("Words with f, g, h", "Main"),
} | {
    ("Blend Sounds 1", "Main"),
    ("Beginning Sounds 2", "Basic"),
}
EXPECTED_LIVE_QUEUE = EXPECTED_BEGINNING_QUARANTINE_QUEUE - {
    ("Words: End Sound", "Main"),
    ("Words with m & n", "Main"),
    ("Short Vowel Sound a", "Main"),
} | {
    ("Short Vowel Sound a", "Practice 1"),
    ("Short Vowel Sound e", "Main"),
    ("Short Vowel Sound o", "Basic"),
}
QUARANTINED_BEGINNING_TITLES = {
    "Words with b, c, d",
    "Words with f, g, h",
    "Words with j, k, l",
    "Words with m, n, p",
    "Other Words",
}


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CatalogIndex(CATALOG_PATH)
        cls.curriculum = ReadingCurriculum.load(CURRICULUM_PATH, cls.catalog)

    def _plan_payload(
        self,
        snapshot: AssignmentSnapshot,
        plan: QueuePlan,
        day: int,
        **details: object,
    ) -> dict[str, object]:
        return create_plan_payload(
            student="Student A",
            snapshot=snapshot,
            plan=plan,
            curriculum=self.curriculum,
            catalog_path=CATALOG_PATH,
            curriculum_path=CURRICULUM_PATH,
            generated_at=datetime(2026, 9, day),
            **details,
        )

    def test_current_records_preserve_the_diverse_ten_item_queue(self) -> None:
        scores = read_attempt_scores(Path("student-records/student-a-lesson-attempts.csv"), "Student A")

        plan = build_queue_plan(
            self.curriculum,
            scores,
            EXPECTED_LIVE_QUEUE,
            quarantined_titles={
                title: "active quarantine"
                for title in QUARANTINED_BEGINNING_TITLES | {"Words: End Sound"}
            },
        )

        self.assertEqual({activity.key for activity in plan.desired}, EXPECTED_LIVE_QUEUE)
        self.assertEqual(plan.actions, ())

    def test_mastery_sync_disables_score_history_cache(self) -> None:
        cache = Mock()

        self.assertIsNone(
            _history_lookup_for_run(
                Namespace(sync=True, full_score_scan=False),
                cache,
            )
        )
        self.assertIs(
            _history_lookup_for_run(
                Namespace(sync=False, full_score_scan=False),
                cache,
            ),
            cache.lookup,
        )

    def test_quarantine_removes_every_variant_of_a_family_and_refills_queue(self) -> None:
        scores = _historical_planner_scores()

        plan = build_queue_plan(
            self.curriculum,
            scores,
            FIRST_QUARANTINE_QUEUE,
            quarantined_titles={
                title: "active through 2026-10-10" for title in QUARANTINED_BEGINNING_TITLES
            },
        )
        desired = {activity.key for activity in plan.desired}

        self.assertEqual(len(desired), 10)
        self.assertEqual(desired, EXPECTED_BEGINNING_QUARANTINE_QUEUE)
        self.assertFalse(any(title in QUARANTINED_BEGINNING_TITLES for title, _ in desired))
        self.assertIn(("Blend Sounds 1", "Main"), desired)
        self.assertIn(("Beginning Sounds 2", "Basic"), desired)
        removals = [action for action in plan.actions if action.kind == "remove"]
        self.assertEqual(
            {action.title for action in removals},
            {"Words with f, g, h", "Words with m, n, p"},
        )
        self.assertTrue(all("quarantined" in action.reason for action in removals))

    def test_queue_target_relaxes_diversity_cap_when_quarantines_leave_nine(self) -> None:
        scores = _historical_planner_scores()
        current = EXPECTED_BEGINNING_QUARANTINE_QUEUE - {("Words: End Sound", "Main")}
        quarantined = QUARANTINED_BEGINNING_TITLES | {"Words: End Sound"}

        plan = build_queue_plan(
            self.curriculum,
            scores,
            current,
            quarantined_titles={title: "active quarantine" for title in quarantined},
        )
        desired = {activity.key for activity in plan.desired}

        self.assertEqual(len(desired), 10)
        self.assertIn(("Short Vowel Sound e", "Basic"), desired)
        addition = next(
            action for action in plan.actions if action.key == ("Short Vowel Sound e", "Basic")
        )
        self.assertIn("queue-target fallback", addition.reason)

    def test_unattempted_stretch_is_pinned_and_low_score_rotates_without_forgetting(self) -> None:
        scores = _historical_planner_scores()
        current = set(PRE_QUARANTINE_QUEUE)

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
        scores = _historical_planner_scores()
        scores[("Words with f, g, h", "Main")] = (65,)
        blend_track = next(
            track for track in self.curriculum.tracks if track.track_id == "three_phoneme_blending"
        )
        for activity in blend_track.activities:
            scores[activity.key] = (100,)

        plan = build_queue_plan(self.curriculum, scores, EXPECTED_DIVERSE_QUEUE)

        self.assertIn(("Words with f, g, h", "Main"), {activity.key for activity in plan.desired})

    def test_completed_vowel_track_rotates_in_next_deferred_vowel(self) -> None:
        scores = _historical_planner_scores()
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
        current = set(PRE_QUARANTINE_QUEUE)
        current.remove(("Blend Sounds 2", "Practice 1"))
        current.add(("Blend Sounds 2", "Basic"))
        scores = {
            ("Blend Sounds 2", "Basic"): (85, 92, 90),
            ("Make New Words", "Basic"): (100,),
            ("Words: End Sound", "Basic"): (100,),
            ("Words: End Sound", "Main"): (83,),
            ("Blend Syllables", "Basic"): (100,),
            ("Blend Syllables", "Main"): (100,),
            ("Blend Syllables", "Practice 1"): (100,),
            ("Blend Syllables", "Practice 2"): (100,),
            ("Short Vowel Sound a", "Basic"): (100,),
            ("Short Vowel Sound i", "Basic"): (100,),
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
        payload = self._plan_payload(
            snapshot,
            plan,
            9,
            new_attempt_records=0,
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
        payload = self._plan_payload(
            snapshot,
            plan,
            10,
            new_attempt_records=0,
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

    def test_reviewed_plan_is_rejected_when_quarantine_state_changes(self) -> None:
        snapshot = AssignmentSnapshot((), ())
        plan = build_queue_plan(self.curriculum, {}, set())
        payload = self._plan_payload(
            snapshot,
            plan,
            11,
            new_attempt_records=0,
        )
        quarantine = LessonQuarantine(
            "Student A",
            "Words with b, c, d",
            date(2026, 9, 11),
            date(2026, 10, 11),
            "low score",
        )

        with self.assertRaisesRegex(AutomationError, "quarantine_state_sha256"):
            validate_reviewed_plan(
                payload,
                student="Student A",
                snapshot=snapshot,
                catalog_path=CATALOG_PATH,
                curriculum_path=CURRICULUM_PATH,
                curriculum=self.curriculum,
                active_quarantines=(quarantine,),
            )

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
        payload = self._plan_payload(
            snapshot,
            plan,
            9,
            new_attempt_records=1,
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


def _historical_planner_scores() -> dict[tuple[str, str], tuple[int, ...]]:
    """Freeze scenario tests at the score state their queue fixtures describe."""
    return {
        ("Beginning Sounds 2", "Basic"): (83, 70, 75, 79, 94),
        ("Blend Sounds 1", "Basic"): (90, 80, 100, 100),
        ("Blend Sounds 2", "Basic"): (85, 92, 92),
        ("Blend Sounds 2", "Main"): (69, 75, 100),
        ("Blend Sounds 2", "Practice 1"): (67,),
        ("Blend Syllables", "Basic"): (100,),
        ("Blend Syllables", "Main"): (91, 94),
        ("Blend Syllables", "Practice 1"): (100,),
        ("Blend Syllables", "Practice 2"): (93, 100),
        ("Ending Sound", "Basic"): (58,),
        ("Make New Words", "Basic"): (92, 71, 100, 85, 92, 100),
        ("Make New Words", "Main"): (92,),
        ("Middle Sound", "Basic"): (50,),
        ("Short Vowel Sound a", "Basic"): (89, 100),
        ("Short Vowel Sound a", "Main"): (84,),
        ("Short Vowel Sound i", "Basic"): (100,),
        ("Short Vowel Sound u", "Basic"): (89, 100),
        ("Word Families", "Basic"): (72, 69, 69),
        ("Words with b, c, d", "Main"): (92, 39),
        ("Words: End Sound", "Basic"): (100,),
        ("Words: End Sound", "Main"): (83, 56, 68, 69),
    }


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
