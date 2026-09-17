from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from dashboard import MAX_PARENT_REQUESTS, PARENT_IDLE_SECONDS, SyncJob
from khan_kids.adb import AutomationError
from khan_kids.records import write_json_atomic

CHANGE = {"grade": "Kindergarten", "title": "Lowercase l", "variant": "Main", "action": "assign"}


def verified_report(student, assignment):
    assigned = (
        [{"title": assignment["title"], "variant": assignment["variant"]}]
        if assignment["action"] == "assign"
        else []
    )
    return {
        "student": student,
        "manual_change": assignment,
        "status": "applied",
        "assigned": assigned,
        "queue_count": len(assigned),
    }


class AssignmentQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        catalog = patch("dashboard.CatalogIndex")
        self.addCleanup(catalog.stop)
        catalog.start()
        factory = patch("dashboard.ManualAssignmentSession")
        self.addCleanup(factory.stop)
        self.factory = factory.start()
        self.session = self.factory.return_value.__enter__.return_value
        self.session.apply.side_effect = verified_report
        self.job = SyncJob(self.root, self.root / "khan-mastery-sync")
        self.addCleanup(self.job.wait)

    def wait_for_warm(self):
        with self.job.condition:
            self.assertTrue(
                self.job.condition.wait_for(lambda: self.job.warm_deadline is not None, timeout=5)
            )

    def test_fifo_requests_reuse_one_session_and_duplicate_pending_click_is_ignored(self):
        entered, release = threading.Event(), threading.Event()

        def apply(student, assignment):
            if self.session.apply.call_count == 1:
                entered.set()
                release.wait(5)
            return verified_report(student, assignment)

        self.session.apply.side_effect = apply
        self.assertTrue(self.job.start("Student A", CHANGE))
        self.assertTrue(entered.wait(5))
        second = {**CHANGE, "variant": "Practice 1"}
        self.assertTrue(self.job.start("Student A", second))
        self.assertTrue(self.job.start("Student A", second))
        self.assertEqual(
            [r["state"] for r in self.job.snapshot()["assignment_requests"]], ["running", "queued"]
        )
        release.set()
        self.job.wait()
        self.factory.assert_called_once()
        self.assertEqual(
            [call.args[1]["variant"] for call in self.session.apply.call_args_list],
            ["Main", "Practice 1"],
        )
        self.assertEqual(
            [r["state"] for r in self.job.snapshot()["assignment_requests"]],
            ["succeeded", "succeeded"],
        )
        self.assertEqual(self.job.requests_path.stat().st_mode & 0o777, 0o600)

    def test_sixty_second_idle_window_accepts_followup_without_logout(self):
        self.assertEqual(PARENT_IDLE_SECONDS, 60)
        self.job.start("Student A", CHANGE)
        self.wait_for_warm()
        before = self.job.snapshot()
        self.assertEqual(before["teacher_session"], "warm")
        self.assertGreaterEqual(before["teacher_idle_seconds"], 59)
        self.factory.return_value.__exit__.assert_not_called()
        self.job.start("Student A", {**CHANGE, "variant": "Practice 1"})
        with self.job.condition:
            self.assertTrue(
                self.job.condition.wait_for(
                    lambda: len([r for r in self.job.requests if r["state"] == "succeeded"]) == 2,
                    timeout=5,
                )
            )
        self.factory.assert_called_once()

    def test_idle_timeout_logs_out_and_releases_worker(self):
        exited = threading.Event()
        self.factory.return_value.__exit__.side_effect = lambda *args: exited.set() or False
        with patch("dashboard.PARENT_IDLE_SECONDS", 0.02):
            self.job.start("Student A", CHANGE)
            self.assertTrue(exited.wait(5))
            self.job.wait()
        self.assertEqual(self.job.snapshot()["teacher_session"], "closed")

    def test_failure_blocks_remaining_requests_without_automatic_retry(self):
        entered, release = threading.Event(), threading.Event()

        def fail(*args):
            entered.set()
            release.wait(5)
            raise AutomationError("unexpected tablet screen")

        self.session.apply.side_effect = fail
        self.job.start("Student A", CHANGE)
        self.assertTrue(entered.wait(5))
        self.job.start("Student A", {**CHANGE, "variant": "Practice 1"})
        release.set()
        self.job.wait()
        self.assertEqual(
            [r["state"] for r in self.job.snapshot()["assignment_requests"]], ["failed", "blocked"]
        )
        self.session.apply.assert_called_once()
        self.assertTrue((self.root / "INCIDENTS.md").exists())

    def test_restart_preserves_but_never_replays_unfinished_requests(self):
        write_json_atomic(
            self.job.requests_path,
            [
                {
                    "id": "synthetic-request",
                    "student": "Student A",
                    "assignment": CHANGE,
                    "state": "running",
                    "error": None,
                }
            ],
        )
        restored = SyncJob(self.root, self.root / "khan-mastery-sync")
        self.assertEqual(restored.snapshot()["assignment_requests"][0]["state"], "blocked")
        self.assertIsNone(restored.thread)
        self.factory.assert_not_called()

    def test_queue_is_bounded_and_rejects_writes_when_dashboard_stopping(self):
        self.job.thread = Mock()
        self.job.thread.is_alive.return_value = True
        for index in range(MAX_PARENT_REQUESTS):
            self.assertTrue(
                self.job.start("Student A", {**CHANGE, "title": "Synthetic lesson " + str(index)})
            )
        self.assertFalse(self.job.start("Student A", CHANGE))
        self.job.thread = None
        self.job.stopping = True
        self.assertFalse(self.job.start("Student A", CHANGE))
        self.assertEqual(len(self.job.requests), MAX_PARENT_REQUESTS)

    def test_unassign_requires_verified_absence_even_with_zero_queue(self):
        assignment = {**CHANGE, "action": "unassign"}
        self.job.start("Student A", assignment)
        self.job.wait()
        self.assertEqual(self.job.snapshot()["state"], "succeeded")
        self.assertEqual(self.job.snapshot()["report"]["queue_count"], 0)
        self.assertEqual(json.loads(self.job.requests_path.read_text())[0]["state"], "succeeded")

    def test_regular_sync_cannot_overlap_warm_parent_session(self):
        self.job.start("Student A", CHANGE)
        self.wait_for_warm()
        self.assertFalse(self.job.start("Student A"))

    def test_parent_requests_wait_for_running_mastery_sync_to_release_tablet(self):
        entered, release = threading.Event(), threading.Event()
        process = Mock()

        def lines():
            entered.set()
            release.wait(5)
            yield (
                json.dumps({"dashboard_report": {"student": "Student A", "status": "no_op"}}) + "\n"
            )

        process.stdout = lines()
        process.wait.return_value = 0
        with patch("dashboard.subprocess.Popen") as popen:
            popen.return_value.__enter__.return_value = process
            self.job.start("Student A")
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.job.start("Student A", CHANGE))
            self.factory.assert_not_called()
            release.set()
            self.job.wait()
        self.session.apply.assert_called_once_with("Student A", CHANGE)

    def test_rejected_queue_write_never_acknowledges_or_executes_request(self):
        with (
            patch.object(self.job, "_save_requests", side_effect=OSError("no space")),
            self.assertRaises(OSError),
        ):
            self.job.start("Student A", CHANGE)
        self.assertEqual(self.job.requests, [])
        self.assertIsNone(self.job.thread)
        self.factory.assert_not_called()

    def test_custom_workflow_never_reads_or_rewrites_native_parent_requests(self):
        requests = [
            {
                "id": "synthetic-request",
                "student": "Student A",
                "assignment": CHANGE,
                "state": "running",
                "error": None,
            }
        ]
        write_json_atomic(self.job.requests_path, requests)
        custom = SyncJob(self.root, self.root / "custom-family-workflow")
        self.assertEqual(custom.snapshot()["assignment_requests"], [])
        self.assertEqual(json.loads(self.job.requests_path.read_text()), requests)
