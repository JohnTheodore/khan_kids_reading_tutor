from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AndroidDevice, AutomationError
from khan_kids.automation import ActionResult
from khan_kids.manual_session import ManualAssignmentSession
from khan_kids.reports import AssignmentRow, AssignmentSnapshot
from khan_kids.ui import Rect

ROOT = Path(__file__).resolve().parents[1]
CHANGE = {"grade": "Kindergarten", "title": "Lowercase l", "variant": "Main", "action": "assign"}


class ManualSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        (self.root / "data/reading-ela-archive.json").write_bytes(
            (ROOT / "data/reading-ela-archive.json").read_bytes()
        )
        self.device = Mock(spec=AndroidDevice)
        self.device.awake_session.return_value = nullcontext()
        self.automation = Mock()
        self.automation.student = "Student A"
        self.automation.set_catalog_assignment.return_value = (
            ActionResult("checked", "Lowercase l", "Main", "saved"),
            {"Student A": "unchecked", "Student B": "unchecked"},
        )
        self.automation.inspect_catalog_assignment.return_value = {
            "Student A": "checked",
            "Student B": "unchecked",
        }
        self.automation.scan_assignments.return_value = AssignmentSnapshot(
            (AssignmentRow("Lowercase l", "Main", "today", Rect(0, 0, 1, 1), None, None),), ()
        )
        for target, value in (
            ("AndroidDevice", self.device),
            ("KhanKidsAutomation", self.automation),
        ):
            patcher = patch("khan_kids.manual_session." + target, return_value=value)
            self.addCleanup(patcher.stop)
            setattr(self, target, patcher.start())
        self.launcher = patch("khan_kids.manual_session.ensure_khan_kids_open")
        self.addCleanup(self.launcher.stop)
        self.launch = self.launcher.start()
        self.secrets = patch("khan_kids.manual_session.local_secrets_provider")
        self.addCleanup(self.secrets.stop)
        self.secrets.start()

    def session(self):
        return ManualAssignmentSession(self.root, "synthetic-usb", Mock())

    def test_direct_edit_never_opens_score_histories_or_plans_mastery(self):
        with self.session() as session:
            report = session.apply("Student A", CHANGE)
            self.automation.return_to_profile_chooser.assert_not_called()
        self.assertEqual(report["queue_count"], 1)
        self.assertEqual(report["manual_change"], CHANGE)
        self.assertEqual(report["new_scores"], [])
        self.automation.set_catalog_assignment.assert_called_once_with(
            "Kindergarten", "Lowercase l", "Main", assigned=True, reset_to_top=True
        )
        self.assertFalse(
            self.automation.scan_assignments.call_args.kwargs["include_score_histories"]
        )
        self.automation.scan_score_histories.assert_not_called()
        self.automation.return_to_profile_chooser.assert_called_once()
        self.assertEqual(
            (self.root / "private/student-a-manual-operation.json").stat().st_mode & 0o777, 0o600
        )

    def test_same_reader_reuses_backend_login_and_awake_session(self):
        with self.session() as session:
            session.apply("Student A", CHANGE)
            session.apply("Student A", CHANGE)
        self.AndroidDevice.assert_called_once()
        self.device.enable_ui_backend.assert_called_once()
        self.device.awake_session.assert_called_once()
        self.launch.assert_called_once()
        self.KhanKidsAutomation.assert_called_once()

    def test_already_unassigned_variant_is_a_verified_noop(self):
        self.automation.set_catalog_assignment.return_value = (None, {"Student A": "unchecked"})
        self.automation.inspect_catalog_assignment.return_value = {"Student A": "unchecked"}
        self.automation.scan_assignments.return_value = AssignmentSnapshot((), ())
        with self.session() as session:
            report = session.apply("Student A", {**CHANGE, "action": "unassign"})
        self.assertEqual(report["status"], "no_op")
        self.assertEqual(report["queue_count"], 0)
        self.assertEqual(report["unchecked"], [])
        self.assertFalse((self.root / "student-records/student-a-assignment-actions.csv").exists())

    def test_verification_rejects_changes_to_another_child(self):
        self.automation.inspect_catalog_assignment.return_value = {
            "Student A": "checked",
            "Student B": "checked",
        }
        with (
            self.assertRaisesRegex(AutomationError, "checkbox verification"),
            self.session() as session,
        ):
            session.apply("Student A", CHANGE)
        self.automation.return_to_profile_chooser.assert_not_called()
        self.assertFalse((self.root / "private/student-a-reading-plan.json").exists())
        self.assertEqual(
            len(list((self.root / "private/dashboard-debug").glob("failure-*/error.txt"))), 1
        )
        journal = json.loads((self.root / "private/student-a-manual-operation.json").read_text())
        self.assertEqual(journal["status"], "interrupted")
        self.assertEqual(journal["applied"][0]["title"], "Lowercase l")

    def test_verification_rejects_missing_live_assignment(self):
        self.automation.scan_assignments.return_value = AssignmentSnapshot((), ())
        with self.assertRaisesRegex(AutomationError, "live queue"), self.session() as session:
            session.apply("Student A", CHANGE)
        self.automation.return_to_profile_chooser.assert_not_called()

    def test_retains_previous_score_evidence_without_claiming_new_scores(self):
        (self.root / "private").mkdir()
        evidence = [
            {"title": "Lowercase l", "variant": "Main", "scores": [88], "status": "practicing"}
        ]
        (self.root / "private/student-a-reading-plan.json").write_text(
            json.dumps({"student": "Student A", "score_evidence": evidence})
        )
        with self.session() as session:
            report = session.apply("Student A", CHANGE)
        self.assertEqual(report["assigned"][0]["scores"], [88])
        self.assertEqual(report["new_scores"], [])

    def test_unknown_student_fails_before_ui_edits(self):
        with self.assertRaisesRegex(AutomationError, "student"), self.session() as session:
            session.apply("Unconfigured reader", CHANGE)
        self.automation.set_catalog_assignment.assert_not_called()

    def test_parent_logout_failure_propagates_after_verified_edit(self):
        self.automation.return_to_profile_chooser.side_effect = AutomationError("logout blocked")
        with self.assertRaisesRegex(AutomationError, "logout blocked"), self.session() as session:
            report = session.apply("Student A", CHANGE)
        self.assertEqual(report["status"], "applied")
        self.assertTrue((self.root / "private/student-a-reading-plan.json").exists())

    def test_adjacent_batch_resets_once_and_scans_queue_once(self):
        following = iter([{**CHANGE, "title": "Lowercase m"}, {**CHANGE, "title": "Lowercase n"}])
        self.automation.set_catalog_assignment.side_effect = (
            lambda grade, title, variant, **kwargs: (
                ActionResult("checked", title, variant, "saved"),
                {"Student A": "unchecked", "Student B": "unchecked"},
            )
        )
        self.automation.scan_assignments.return_value = AssignmentSnapshot(
            tuple(
                AssignmentRow(f"Lowercase {letter}", "Main", "today", Rect(0, 0, 1, 1), None, None)
                for letter in "lmn"
            ),
            (),
        )
        with self.session() as session:
            reports = session.apply_many("Student A", CHANGE, lambda: next(following, None))
        self.assertEqual(len(reports), 3)
        self.assertEqual([report["queue_count"] for report in reports], [3, 3, 3])
        self.assertEqual(
            [
                call.kwargs["reset_to_top"]
                for call in self.automation.set_catalog_assignment.call_args_list
            ],
            [True, False, False],
        )
        self.assertTrue(
            all(
                not call.kwargs["reset_to_top"]
                for call in self.automation.inspect_catalog_assignment.call_args_list
            )
        )
        self.automation.scan_assignments.assert_called_once()
        journal = json.loads((self.root / "private/student-a-manual-operation.json").read_text())
        self.assertEqual(
            [entry["status"] for entry in journal["batch_operations"]], ["applied"] * 3
        )

    def test_partial_batch_failure_reconciles_first_save_without_replay(self):
        following = iter([{**CHANGE, "title": "Lowercase m"}])
        self.automation.inspect_catalog_assignment.side_effect = [
            {"Student A": "checked", "Student B": "unchecked"},
            {"Student A": "checked", "Student B": "checked"},
        ]
        with self.session() as session:
            with self.assertRaisesRegex(AutomationError, "checkbox verification"):
                session.apply_many("Student A", CHANGE, lambda: next(following, None))
            self.assertEqual(
                [report["manual_change"] for report in session.verified_reports], [CHANGE]
            )
        self.assertEqual(self.automation.set_catalog_assignment.call_count, 2)
        self.automation.scan_assignments.assert_called_once()
        journal = json.loads((self.root / "private/student-a-manual-operation.json").read_text())
        self.assertEqual(journal["status"], "interrupted")
        self.assertEqual(journal["recovery"]["status"], "partial")

    def test_batch_rejects_same_variant_twice_before_second_ui_write(self):
        with (
            self.assertRaisesRegex(AutomationError, "distinct variants"),
            self.session() as session,
        ):
            session.apply_many("Student A", CHANGE, lambda: CHANGE)
        self.automation.set_catalog_assignment.assert_called_once()
