from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AutomationError
from khan_kids.automation import ActionResult
from khan_kids.catalog import CatalogIndex
from khan_kids.curriculum import Activity, ReadingCurriculum
from khan_kids.manual_assignments import ManualAssignments, ManualChange, plan_parent_queue
from khan_kids.planner import build_queue_plan
from khan_kids.reports import AssignmentRow, AssignmentSnapshot
from khan_kids.timing import TimingRecorder
from khan_kids.ui import Rect
from reading_workflow import _apply_reviewed_plan, create_plan_payload, validate_reviewed_plan

CATALOG = Path("data/reading-ela-archive.json")
CURRICULUM = Path("data/reading-curriculum.json")


def snapshot(activities: tuple[Activity, ...]) -> AssignmentSnapshot:
    return AssignmentSnapshot(
        tuple(
            AssignmentRow(a.title, a.variant, "Today", Rect(0, 0, 100, 100), None, None)
            for a in activities
        ),
        (),
    )


class ManualAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CatalogIndex(CATALOG)
        cls.curriculum = ReadingCurriculum.load(CURRICULUM, cls.catalog)
        cls.base = build_queue_plan(cls.curriculum, {}, set()).desired
        cls.extra = Activity("Kindergarten", "Lowercase l", "Main")

    def plan(self, policy, current=None, scores=None, **kwargs):
        return plan_parent_queue(
            self.curriculum,
            self.catalog,
            scores or {},
            {a.key for a in self.base} if current is None else current,
            policy,
            **kwargs,
        )

    def payload(self, plan, observed, policy, **kwargs):
        return create_plan_payload(
            student="Student A",
            snapshot=observed,
            plan=plan,
            curriculum=self.curriculum,
            catalog_path=CATALOG,
            curriculum_path=CURRICULUM,
            new_attempt_records=0,
            generated_at=datetime(2026, 9, 17),
            manual_policy=policy,
            **kwargs,
        )

    def validate(self, payload, observed, policy, **kwargs):
        return validate_reviewed_plan(
            payload,
            student="Student A",
            snapshot=observed,
            catalog_path=CATALOG,
            curriculum_path=CURRICULUM,
            curriculum=self.curriculum,
            manual_policy=policy,
            **kwargs,
        )

    def test_assign_changes_only_one_variant_and_is_idempotent(self):
        change = ManualChange(self.extra, "assign")
        policy = ManualAssignments("Student A").changed(change)
        plan = self.plan(policy, change=change)
        self.assertEqual(len(plan.desired), 11)
        self.assertEqual([(a.kind, a.key) for a in plan.actions], [("add", self.extra.key)])
        current = {a.key for a in plan.desired}
        repeated = self.plan(policy, current, change=change)
        self.assertEqual(repeated.actions, ())
        synced = self.plan(policy, current)
        self.assertEqual({a.key for a in synced.desired}, current)
        self.assertEqual(synced.actions, ())

    def test_manual_pin_is_not_trimmed_by_quarantine_or_prerequisites(self):
        policy = ManualAssignments("Student A").changed(ManualChange(self.extra, "assign"))
        plan = self.plan(policy, quarantined_titles={self.extra.title: "held"})
        self.assertIn(self.extra, plan.desired)
        self.assertEqual(len(plan.desired), 11)

    def test_mastered_pin_is_removed_with_native_score_reason(self):
        policy = ManualAssignments("Student A").changed(ManualChange(self.extra, "assign"))
        current = {a.key for a in self.base} | {self.extra.key}
        plan = self.plan(policy, current, {self.extra.key: (88, 100)})
        self.assertEqual(len(plan.desired), 10)
        self.assertNotIn(self.extra, plan.desired)
        action = next(a for a in plan.actions if a.key == self.extra.key)
        self.assertEqual(action.kind, "remove")
        self.assertTrue(action.reason.startswith("mastered:"))
        self.assertIn("100%", action.reason)

    def test_durable_mastery_prevents_manual_pin_regression(self):
        policy = ManualAssignments("Student A").changed(ManualChange(self.extra, "assign"))
        plan = self.plan(policy, mastered_keys={self.extra.key})
        self.assertNotIn(self.extra, plan.desired)

    def test_unassign_changes_only_one_and_blocks_exact_variant(self):
        activity = self.base[0]
        change = ManualChange(activity, "unassign")
        policy = ManualAssignments("Student A").changed(change)
        plan = self.plan(policy, change=change)
        self.assertEqual(len(plan.desired), 9)
        self.assertEqual([(a.kind, a.key) for a in plan.actions], [("remove", activity.key)])
        current = {a.key for a in plan.desired}
        repeated = self.plan(policy, current, change=change)
        self.assertEqual(repeated.actions, ())
        synced = self.plan(policy, current)
        self.assertNotIn(activity.key, {a.key for a in synced.desired})
        self.assertEqual(len(synced.desired), 10)
        policy = policy.changed(ManualChange(activity, "assign"))
        self.assertNotIn(activity.key, policy.excluded_keys)

    def test_topups_pause_above_ten_and_resume_below(self):
        extras = tuple(Activity("Kindergarten", f"Lowercase {letter}", "Main") for letter in "lmno")
        policy = ManualAssignments("Student A", extras)
        current = {a.key for a in self.base} | {a.key for a in extras}
        # Two automatic lessons master: keep the other eight plus four manual.
        scores = {a.key: (100,) for a in self.base[:2]}
        plan = self.plan(policy, current, scores)
        self.assertEqual(len(plan.desired), 12)
        self.assertFalse(any(a.kind == "add" for a in plan.actions))
        self.assertFalse(any("promote to" in a.reason for a in plan.actions))
        scores.update({a.key: (100,) for a in extras})
        current = {a.key for a in plan.desired}
        next_plan = self.plan(policy, current, scores)
        self.assertEqual(len(next_plan.desired), 10)
        self.assertEqual(sum(a.kind == "add" for a in next_plan.actions), 2)
        # A second consecutive reconciliation is a fixed point.
        repeated = self.plan(policy, {a.key for a in next_plan.desired}, scores)
        self.assertEqual(repeated.actions, ())

    def test_policy_roundtrip_is_private_student_scoped_and_stable(self):
        change = ManualChange(self.extra, "assign")
        policy = ManualAssignments("Student A").changed(change)
        self.assertEqual(policy.changed(change).digest, policy.digest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            policy.save(path)
            self.assertEqual(ManualAssignments.load(path, "Student A", self.catalog), policy)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(AutomationError):
                ManualAssignments.load(path, "Student B", self.catalog)
            raw = policy.as_dict()
            raw["excluded"] = raw["assigned"]
            path.write_text(json.dumps(raw))
            with self.assertRaises(AutomationError):
                ManualAssignments.load(path, "Student A", self.catalog)

    def test_assignment_input_is_whitelisted_and_catalog_validated(self):
        valid = ManualChange(self.extra, "assign").as_dict()
        self.assertEqual(ManualChange.from_dict(valid, self.catalog).activity, self.extra)
        invalid = [
            {**valid, "action": "exec"},
            {**valid, "variant": "Practice 900"},
            {**valid, "grade": "Mars"},
            {**valid, "title": "not a lesson"},
            {**valid, "variant": True},
            {**valid, "command": "arbitrary"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(AutomationError):
                ManualChange.from_dict(payload, self.catalog)

    def test_reviewed_overflow_is_validated_against_saved_intent(self):
        policy = ManualAssignments("Student A").changed(ManualChange(self.extra, "assign"))
        observed = snapshot(self.base)
        plan = self.plan(policy)
        payload = self.payload(plan, observed, policy)
        desired, _ = self.validate(payload, observed, policy)
        self.assertEqual(len(desired), 11)
        changed = policy.changed(ManualChange(self.extra, "unassign"))
        with self.assertRaisesRegex(AutomationError, "manual_state_sha256"):
            self.validate(payload, observed, changed)
        payload["desired_assignments"].append(
            Activity("Kindergarten", "Lowercase m", "Main").as_dict()
        )
        with self.assertRaisesRegex(AutomationError, "parent preferences"):
            self.validate(payload, observed, policy)

    def test_immediate_parent_change_works_with_empty_or_small_queue(self):
        for action in ("assign", "unassign"):
            change = ManualChange(self.extra, action)
            policy = ManualAssignments("Student A").changed(change)
            plan = self.plan(policy, set(), change=change)
            observed = snapshot(())
            payload = self.payload(plan, observed, policy, manual_change=change)
            desired, _ = self.validate(payload, observed, policy)
            self.assertEqual(len(desired), action == "assign")

    def test_parent_click_preserves_unrelated_unknown_and_ambiguous_rows(self):
        change = ManualChange(self.extra, "assign")
        policy = ManualAssignments("Student A").changed(change)
        current = {("An unrelated assignment", "Direct"), ("Uppercase A", "Main")}
        plan = self.plan(policy, current, change=change)
        self.assertEqual({a.key for a in plan.desired}, current | {self.extra.key})
        self.assertEqual([(a.kind, a.key) for a in plan.actions], [("add", self.extra.key)])
        observed = snapshot(tuple(a for a in plan.desired if a.key in current))
        self.validate(self.payload(plan, observed, policy, manual_change=change), observed, policy)

    def test_tampered_parent_removal_cannot_fabricate_mastery(self):
        change = ManualChange(self.base[0], "unassign")
        policy = ManualAssignments("Student A").changed(change)
        observed = snapshot(self.base)
        plan = self.plan(policy, change=change)
        payload = self.payload(plan, observed, policy, manual_change=change)
        payload["actions"][0]["reason"] = "mastered: invented evidence"
        with self.assertRaisesRegex(AutomationError, "parent actions"):
            self.validate(payload, observed, policy)

    def test_existing_native_save_journal_and_fixed_point_handle_overflow(self):
        change = ManualChange(self.extra, "assign")
        policy = ManualAssignments("Student A").changed(change)
        observed = snapshot(self.base)
        plan = self.plan(policy, change=change)
        payload = self.payload(plan, observed, policy, manual_change=change)
        automation = Mock()
        automation.device.timing = TimingRecorder()
        automation.assign.return_value = ActionResult(
            "checked", self.extra.title, self.extra.variant, "saved; final verification pending"
        )
        verified = snapshot(plan.desired)
        automation.scan_assignments.side_effect = [verified, verified]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy.save(root / "policy.json")
            args = Namespace(
                student="Student A",
                catalog=CATALOG,
                curriculum=CURRICULUM,
                max_actions=1,
                today=date(2026, 9, 17),
                apply_plan=root / "plan.json",
                manual_policy_path=root / "policy.json",
                attempts=root / "attempts.csv",
            )
            result = _apply_reviewed_plan(
                args=args,
                payload=payload,
                snapshot=observed,
                automation=automation,
                actions_path=root / "actions.csv",
                report_path=root / "report.md",
                curriculum=self.curriculum,
                catalog=self.catalog,
            )
            self.assertEqual(result["status"], "applied")
            self.assertEqual(len(result["verified_assignments"]), 11)
            self.assertEqual(result["operation_journal"]["operations"][0]["state"], "verified")
            automation.assign.assert_called_once_with(
                self.extra.grade, self.extra.title, self.extra.variant
            )
            automation.unassign.assert_not_called()


if __name__ == "__main__":
    unittest.main()
