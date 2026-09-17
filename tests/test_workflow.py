from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

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
    MasterySyncInterrupted,
    _apply_reviewed_plan,
    _history_lookup_for_run,
    _review_snapshot,
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
    ("Short Vowel Sound a", "Practice 1"),
    ("Short Vowel Sound o", "Basic"),
    ("Beginning Sounds 2", "Basic"),
    ("Blend Sounds 1", "Main"),
    ("Blend Sounds 1", "Practice 1"),
    ("Blend Sounds 2", "Practice 1"),
    ("Short Vowel Sound i", "Main"),
    ("Short Vowel Sound o", "Main"),
} | {
    ("Words with a", "Main"),
    ("Short Vowel Sound e", "Main"),
    ("Short Vowel Sound o", "Practice 1"),
    ("Short Vowel Sound i", "Practice 1"),
    ("Beginning Sounds 2", "Main"),
    ("Blend Sounds 1", "Practice 2"),
    ("Blend Sounds 2", "Practice 2"),
}
SEPTEMBER_15_ACTUAL_QUEUE = {
    ("Blend Sounds 1", "Practice 2"),
    ("Words with a", "Main"),
    ("Short Vowel Sound e", "Main"),
    ("Short Vowel Sound u", "Main"),
    ("Beginning Sounds 2", "Main"),
    ("Words with b & d", "Main"),
    ("Make New Words", "Main"),
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

    def _apply_plan(
        self,
        *,
        payload: dict[str, object],
        snapshot: AssignmentSnapshot,
        automation: Mock,
        directory: Path,
        day: int,
    ) -> dict[str, object]:
        args = Namespace(
            student="Student A",
            catalog=CATALOG_PATH,
            curriculum=CURRICULUM_PATH,
            max_actions=2,
            today=date(2026, 9, day),
            apply_plan=directory / "plan.json",
        )
        return _apply_reviewed_plan(
            args=args,
            payload=payload,
            snapshot=snapshot,
            automation=automation,
            actions_path=directory / "actions.csv",
            report_path=directory / "sync-log.md",
            curriculum=self.curriculum,
            catalog=self.catalog,
        )

    def test_current_records_plan_exactly_ten_after_new_scores(self) -> None:
        scores = _september_15_planner_scores()

        plan = build_queue_plan(
            self.curriculum,
            scores,
            SEPTEMBER_15_ACTUAL_QUEUE,
            quarantined_titles={
                title: "active quarantine"
                for title in QUARANTINED_BEGINNING_TITLES | {"Words: End Sound"}
            },
            mastered_keys={("Beginning Sounds 2", "Basic")},
        )

        desired = {activity.key for activity in plan.desired}
        self.assertEqual(len(desired), self.curriculum.queue_limit)
        self.assertIn(("Make New Words", "Practice 1"), desired)
        self.assertIn(("Short Vowel Sound u", "Practice 1"), desired)
        self.assertIn(("Beginning Sounds 1", "Basic"), desired)
        self.assertIn(("Rhyming", "Basic"), desired)
        repeated = build_queue_plan(
            self.curriculum,
            scores,
            desired,
            quarantined_titles={
                title: "active quarantine"
                for title in QUARANTINED_BEGINNING_TITLES | {"Words: End Sound"}
            },
            mastered_keys={("Beginning Sounds 2", "Basic")},
        )
        self.assertEqual({activity.key for activity in repeated.desired}, desired)
        self.assertEqual(repeated.actions, ())

    def test_exhausted_reserves_leave_an_underfilled_plan_that_cannot_apply(self) -> None:
        scores = _september_15_planner_scores()
        curriculum = replace(self.curriculum, stretch_pool=self.curriculum.stretch_pool[:-2])
        current = SEPTEMBER_15_ACTUAL_QUEUE
        plan = build_queue_plan(
            curriculum,
            scores,
            current,
            quarantined_titles={
                title: "active quarantine"
                for title in QUARANTINED_BEGINNING_TITLES | {"Words: End Sound"}
            },
            mastered_keys={("Beginning Sounds 2", "Basic")},
        )
        self.assertEqual(len(plan.desired), 8)
        snapshot = AssignmentSnapshot(
            tuple(_activity_row(self.curriculum.activities_by_key[key]) for key in current), ()
        )
        payload = self._plan_payload(snapshot, plan, 15, new_attempt_records=0)
        automation = Mock()
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(AutomationError, "exactly 10"),
        ):
            self._apply_plan(
                payload=payload,
                snapshot=snapshot,
                automation=automation,
                directory=Path(temporary),
                day=15,
            )
        automation.assign.assert_not_called()
        automation.unassign.assert_not_called()

    def test_quarantined_reserves_are_not_used_as_fillers(self) -> None:
        scores = _september_15_planner_scores()
        plan = build_queue_plan(
            self.curriculum,
            scores,
            SEPTEMBER_15_ACTUAL_QUEUE,
            quarantined_titles={
                title: "active quarantine"
                for title in QUARANTINED_BEGINNING_TITLES
                | {"Words: End Sound", "Beginning Sounds 1", "Rhyming"}
            },
            mastered_keys={("Beginning Sounds 2", "Basic")},
        )
        self.assertEqual(len(plan.desired), 8)

    def test_sync_review_reports_gap_without_mutating_when_plan_is_underfilled(self) -> None:
        snapshot = AssignmentSnapshot(
            tuple(
                _activity_row(self.curriculum.activities_by_key[key])
                for key in SEPTEMBER_15_ACTUAL_QUEUE
            ),
            (),
        )
        plan = QueuePlan(
            tuple(self.curriculum.activities_by_key.values())[:8],
            (QueueAction("remove", "Blend Sounds 1", "Practice 2", "", "test"),),
            (),
        )
        automation = Mock()
        automation.device.timing.span.return_value = nullcontext()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("reading_workflow.build_queue_plan", return_value=plan),
        ):
            directory = Path(temporary)
            payload = _review_snapshot(
                args=Namespace(
                    student="Student A",
                    today=date(2026, 9, 15),
                    quarantines=directory / "quarantines.csv",
                    sync=True,
                    full_score_scan=False,
                    catalog=CATALOG_PATH,
                    curriculum=CURRICULUM_PATH,
                    max_actions=20,
                    apply_plan=None,
                ),
                snapshot=snapshot,
                automation=automation,
                catalog=self.catalog,
                curriculum=self.curriculum,
                attempts_path=directory / "attempts.csv",
                actions_path=directory / "actions.csv",
                report_path=directory / "report.md",
                plan_path=directory / "plan.json",
                active_quarantines=(),
                mastered_keys=set(),
            )
            self.assertEqual(payload["status"], "review_required")
            self.assertEqual(payload["queue_gap"], 2)
            self.assertIn("no assignment changes were applied", payload["queue_block_reason"])
            self.assertIn("Queue target blocked", (directory / "report.md").read_text())
        automation.assign.assert_not_called()
        automation.unassign.assert_not_called()

    def test_verified_mastery_is_a_fixed_point_after_live_duplicate_disappears(self) -> None:
        current = EXPECTED_LIVE_QUEUE - {("Beginning Sounds 2", "Main")} | {
            ("Beginning Sounds 2", "Basic")
        }
        durable_scores = _historical_planner_scores()
        live_scores = dict(durable_scores)
        live_scores[("Beginning Sounds 2", "Basic")] = (83, 70, 75, 79, 94, 94)
        quarantines = {
            title: "active quarantine"
            for title in QUARANTINED_BEGINNING_TITLES | {"Words: End Sound"}
        }

        first = build_queue_plan(
            self.curriculum,
            live_scores,
            current,
            quarantined_titles=quarantines,
        )
        mastered = {
            action.key
            for action in first.actions
            if action.kind == "remove" and action.reason.startswith("mastered:")
        }
        second = build_queue_plan(
            self.curriculum,
            durable_scores,
            {activity.key for activity in first.desired},
            quarantined_titles=quarantines,
            mastered_keys=mastered,
        )

        self.assertIn(("Beginning Sounds 2", "Main"), {item.key for item in first.desired})
        self.assertEqual(
            {activity.key for activity in second.desired},
            {activity.key for activity in first.desired},
        )
        self.assertEqual(second.actions, ())

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
        self.assertIn(("Beginning Sounds 1", "Basic"), desired)
        addition = next(
            action for action in plan.actions if action.key == ("Beginning Sounds 1", "Basic")
        )
        self.assertIn("stretch slot", addition.reason)

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
        scores = read_attempt_scores(
            Path("student-records/student-a-lesson-attempts.csv"), "Student A"
        )
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
        shared = tuple(
            activity
            for activity in self.curriculum.activities_by_key.values()
            if activity.key not in {("Blend Sounds 2", "Basic"), ("Blend Sounds 2", "Main")}
        )[:9]
        old = Activity("Preschool (Age 4)", "Blend Sounds 2", "Basic")
        snapshot = AssignmentSnapshot(tuple(_activity_row(item) for item in (*shared, old)), ())
        desired = Activity("Preschool (Age 4)", "Blend Sounds 2", "Main")
        plan = QueuePlan(
            (*shared, desired),
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
            tuple(_activity_row(item) for item in (*shared, desired)), ()
        )
        automation = Mock()
        automation.device.timing.span.return_value = nullcontext()
        after_removal = AssignmentSnapshot(tuple(_activity_row(item) for item in shared), ())
        automation.unassign.return_value = ActionResult(
            "unchecked", "Blend Sounds 2", "Basic", "saved"
        )
        automation.assign.return_value = ActionResult("checked", "Blend Sounds 2", "Main", "saved")
        automation.scan_assignments.side_effect = (after_removal, final_snapshot, final_snapshot)

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            self._apply_plan(
                payload=payload,
                snapshot=snapshot,
                automation=automation,
                directory=temporary_path,
                day=9,
            )

            self.assertEqual(payload["status"], "applied")
            self.assertEqual(len((temporary_path / "actions.csv").read_text().splitlines()), 3)
            self.assertTrue((temporary_path / "plan.json").exists())
            self.assertIn("Applied promotions", (temporary_path / "sync-log.md").read_text())
            self.assertEqual(
                [call[0] for call in automation.method_calls if call[0] in {"assign", "unassign"}],
                ["unassign", "assign"],
            )
            journal = payload["operation_journal"]
            self.assertEqual(journal["status"], "complete")
            self.assertTrue(all(item["state"] == "verified" for item in journal["operations"]))

    def test_interruption_after_full_queue_removal_captures_one_missing_replacement(self) -> None:
        activities = tuple(self.curriculum.activities_by_key.values())[:11]
        desired = activities[:10]
        replacement = desired[-1]
        displaced = activities[-1]
        current_activities = (*desired[:-1], displaced)
        snapshot = AssignmentSnapshot(tuple(_activity_row(item) for item in current_activities), ())
        plan = QueuePlan(
            desired,
            (
                QueueAction(
                    "remove",
                    displaced.title,
                    displaced.variant,
                    displaced.grade,
                    f"mastered: test; promote to {replacement.title} — {replacement.variant}",
                ),
                QueueAction(
                    "add",
                    replacement.title,
                    replacement.variant,
                    replacement.grade,
                    "replacement",
                ),
            ),
            (),
        )
        payload = self._plan_payload(snapshot, plan, 14, new_attempt_records=0)
        after_removal = AssignmentSnapshot(tuple(_activity_row(item) for item in desired[:-1]), ())
        automation = Mock()
        automation.device.timing.span.return_value = nullcontext()
        automation.device.timing.snapshot.return_value = {"wall_seconds": 3.5, "steps": []}
        automation.unassign.return_value = ActionResult(
            "unchecked", displaced.title, displaced.variant, "saved"
        )
        automation.scan_assignments.side_effect = (
            AutomationError("verification hierarchy unavailable"),
            after_removal,
        )

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            with self.assertRaises(MasterySyncInterrupted):
                self._apply_plan(
                    payload=payload,
                    snapshot=snapshot,
                    automation=automation,
                    directory=temporary_path,
                    day=14,
                )

            self.assertEqual(payload["status"], "interrupted")
            self.assertEqual(payload["recovery"]["live_count"], 9)
            self.assertEqual(
                payload["recovery"]["missing_assignments"],
                [{"title": replacement.title, "variant": replacement.variant}],
            )
            operations = payload["operation_journal"]["operations"]
            self.assertEqual([item["state"] for item in operations], ["saved", "planned"])
            automation.assign.assert_not_called()
            report = (temporary_path / "sync-log.md").read_text()
            self.assertIn("Last verified live queue: 9 assignments", report)
            self.assertIn("Applied before interruption promotions\n\nNone.", report)


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


def _september_15_planner_scores() -> dict[tuple[str, str], tuple[int, ...]]:
    """Freeze the seven-item incident and newly observed mastery evidence."""
    scores = _historical_planner_scores()
    scores.update(
        {
            ("Beginning Sounds 2", "Main"): (59,),
            ("Blend Sounds 1", "Main"): (90, 100),
            ("Blend Sounds 1", "Practice 1"): (100,),
            ("Blend Sounds 1", "Practice 2"): (100,),
            ("Blend Sounds 2", "Practice 1"): (67, 78, 100),
            ("Make New Words", "Main"): (92, 88, 94, 100),
            ("Short Vowel Sound a", "Main"): (84, 100),
            ("Short Vowel Sound a", "Practice 1"): (84, 100),
            ("Short Vowel Sound e", "Basic"): (100,),
            ("Short Vowel Sound i", "Main"): (100, 100),
            ("Short Vowel Sound o", "Basic"): (100,),
            ("Short Vowel Sound o", "Main"): (100,),
            ("Short Vowel Sound u", "Main"): (100,),
            ("Words with m & n", "Main"): (58,),
        }
    )
    return scores


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


def _activity_row(activity: Activity) -> AssignmentRow:
    return AssignmentRow(
        title=activity.title,
        variant=activity.variant,
        assigned_date="Today",
        rect=Rect(0, 0, 100, 100),
        score=None,
        score_rect=None,
    )


if __name__ == "__main__":
    unittest.main()
