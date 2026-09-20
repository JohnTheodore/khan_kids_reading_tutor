from __future__ import annotations

import http.client
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from dashboard import (
    MAX_OUTPUT_CHARS,
    DashboardServer,
    SyncJob,
    load_dashboard_token,
    main,
    setup_status,
    sync_progress,
)
from khan_kids.adb import AndroidDevice, AutomationError
from khan_kids.sync_report import build_dashboard_report


class DashboardTests(unittest.TestCase):
    def test_progress_uses_finished_stages_not_elapsed_time(self) -> None:
        output = "Starting phase.review_assignments\n"
        self.assertEqual(sync_progress(output, "running"), 0.2)
        output += "Finished phase.review_assignments (100s)\nFinished phase.plan_queue (1s)\n"
        self.assertEqual(sync_progress(output, "running"), 0.6)
        self.assertEqual(sync_progress(output, "failed"), 0.6)
        self.assertEqual(sync_progress(output, "succeeded"), 1)
        self.assertEqual(sync_progress("", "running"), 0)

    def setUp(self) -> None:
        self.job = Mock()
        self.job.root = Path("unused")
        self.job.serial = None
        self.job.snapshot.return_value = {"state": "idle", "output": "", "returncode": None}
        self.job.start.return_value = True
        self.job.checkin_history.return_value = {
            "version": 1,
            "student": "Student A",
            "events": [],
            "backfill_note": "",
        }
        self.server = DashboardServer(0, self.job, token="T" * 43)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.setup = patch(
            "dashboard.setup_status",
            return_value={
                "ready": True,
                "students": ["Student A"],
                "checks": [],
            },
        )
        self.status = self.setup.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.setup.stop()

    def request(self, method: str, path: str, payload: object = None, **headers) -> tuple:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        defaults = {
            "X-Tutor-Token": self.server.token,
            "Origin": self.server.origin,
            "Content-Type": "application/json",
        }
        defaults.update(headers)
        body = json.dumps(payload) if payload is not None else None
        connection.request(method, path, body=body, headers=defaults)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_assets_and_setup_have_security_headers_without_triggering_sync(self) -> None:
        for path in ("/", "/app.js", "/style.css", "/api/setup", "/api/status"):
            code, body, headers = self.request("GET", path)
            self.assertEqual(code, 200)
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.job.start.assert_not_called()

    def test_history_is_authenticated_and_scoped_to_registered_reader(self) -> None:
        code, body, _ = self.request("GET", "/api/history?student=Student%20A")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["student"], "Student A")
        self.job.checkin_history.assert_called_once_with("Student A")
        self.assertEqual(self.request("GET", "/api/history?student=Unknown")[0], 400)

    def test_auth_origin_and_host_are_required(self) -> None:
        cases = [
            {"X-Tutor-Token": "wrong"},
            {"Origin": "https://untrusted.example"},
            {"Host": "untrusted.example"},
            {"X-Tutor-Token": "é"},
        ]
        for headers in cases:
            for method, path in (("GET", "/api/status"), ("POST", "/api/sync")):
                self.assertEqual(
                    self.request(method, path, {"student": "Student A"}, **headers)[0], 403
                )
        self.job.start.assert_not_called()

    def test_status_sends_phase_but_diagnostics_only_when_requested(self) -> None:
        self.job.snapshot.side_effect = lambda: {
            "state": "running",
            "output": "Starting phase.review_assignments\nStarting phase.plan_queue\n"
            + "x" * 10000,
        }
        code, body, _ = self.request("GET", "/api/status")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["phase"], "phase.plan_queue")
        self.assertEqual(data["output"], "")
        self.assertLess(len(body), 150)
        code, body, _ = self.request("GET", "/api/status?diagnostics=1")
        self.assertEqual(code, 200)
        self.assertIn("Starting phase.review_assignments", json.loads(body)["output"])

    def test_sync_accepts_only_registered_student_and_no_extra_arguments(self) -> None:
        for payload in (
            {"student": "Unknown"},
            {"student": "Student A", "serial": "other"},
            {"student": ["Student A"]},
            [],
            {},
        ):
            self.assertEqual(self.request("POST", "/api/sync", payload)[0], 400)
        self.job.start.assert_not_called()
        self.assertEqual(self.request("POST", "/api/sync", {"student": "Student A"})[0], 202)
        self.job.start.assert_called_once_with("Student A")

    def test_incomplete_setup_and_busy_job_fail_closed(self) -> None:
        self.status.return_value = {"ready": False, "students": ["Student A"]}
        self.assertEqual(self.request("POST", "/api/sync", {"student": "Student A"})[0], 409)
        self.job.start.assert_not_called()
        self.status.return_value["ready"] = True
        self.job.start.return_value = False
        self.assertEqual(self.request("POST", "/api/sync", {"student": "Student A"})[0], 409)

    def test_archived_reader_cannot_launch_sync_and_journey_is_authenticated(self) -> None:
        self.status.return_value["archived_students"] = ["Student A"]
        self.assertEqual(self.request("POST", "/api/sync", {"student": "Student A"})[0], 409)
        self.job.start.assert_not_called()
        self.job.journeys.return_value = {"readers": [{"student": "Student A", "archived": True}]}
        self.assertEqual(self.request("GET", "/api/journey", **{"X-Tutor-Token": "wrong"})[0], 403)
        code, body, _ = self.request("GET", "/api/journey")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["readers"][0]["archived"])
        self.job.start.assert_not_called()

    def test_paths_cannot_expose_private_files(self) -> None:
        for path in (
            "/private/.secrets.json",
            "/../README.md",
            "/%2e%2e/.secrets.json",
            "/api/unknown",
        ):
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_assignment_endpoint_accepts_only_exact_native_catalog_variant(self):
        self.job.root = Path(__file__).resolve().parents[1]
        self.job.workflow = self.job.root / "khan-mastery-sync"
        assignment = {
            "grade": "Kindergarten",
            "title": "Lowercase l",
            "variant": "Main",
            "action": "assign",
        }
        payload = {"student": "Student A", **assignment}
        self.assertEqual(self.request("POST", "/api/assignment", payload)[0], 202)
        self.job.start.assert_called_once_with("Student A", assignment=assignment)
        self.job.start.reset_mock()
        for changes in (
            {"variant": "Practice 900"},
            {"action": "exec"},
            {"student": "Student C"},
            {"title": 123},
            {"grade": "unknown"},
            {"extra": "argument"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.request("POST", "/api/assignment", {**payload, **changes})[0], 400
                )
        self.job.start.assert_not_called()
        self.assertEqual(
            self.request("POST", "/api/assignment", payload, **{"X-Tutor-Token": "wrong"})[0], 403
        )
        self.status.return_value["archived_students"] = ["Student A"]
        self.assertEqual(self.request("POST", "/api/assignment", payload)[0], 409)
        self.status.return_value["archived_students"] = []
        self.job.start.return_value = False
        self.assertEqual(self.request("POST", "/api/assignment", payload)[0], 409)

    def test_custom_engine_cannot_receive_assignment_arguments(self):
        self.job.root = Path(__file__).resolve().parents[1]
        self.job.workflow = self.job.root / "custom-workflow"
        payload = {
            "student": "Student A",
            "grade": "Kindergarten",
            "title": "Lowercase l",
            "variant": "Main",
            "action": "assign",
        }
        self.assertEqual(self.request("POST", "/api/assignment", payload)[0], 400)
        self.job.start.assert_not_called()

    def test_invalid_content_type_and_oversized_body_do_not_start_sync(self) -> None:
        self.assertEqual(
            self.request("POST", "/api/sync", {}, **{"Content-Type": "text/plain"})[0], 400
        )
        self.assertEqual(self.request("POST", "/api/sync", {"student": "x" * 5000})[0], 400)
        self.job.start.assert_not_called()

    def test_latest_result_accepts_only_authenticated_registered_student(self) -> None:
        self.job.latest_report.return_value = None
        self.assertEqual(self.request("GET", "/api/latest?student=Student%20A")[0], 200)
        self.job.latest_report.assert_called_once_with("Student A")
        self.assertEqual(self.request("GET", "/api/latest?student=../secrets")[0], 400)
        self.assertEqual(
            self.request("GET", "/api/latest?student=Student%20A", **{"X-Tutor-Token": "wrong"})[0],
            403,
        )

    def test_post_without_origin_is_rejected_even_with_valid_token(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(
            "POST",
            "/api/sync",
            body=json.dumps({"student": "Student A"}),
            headers={"X-Tutor-Token": self.server.token, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()
        self.job.start.assert_not_called()

    def test_connection_check_and_safe_stop_are_explicit_operations(self) -> None:
        self.job.check_connection.return_value = {
            "ready": True,
            "checks": [{"id": "internet", "label": "Internet", "ok": True}],
        }
        code, body, _ = self.request("POST", "/api/connection-check", {})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ready"])
        self.job.start.assert_not_called()

        self.job.finish_cleanup.return_value = {"ready": True, "cleanup_recovered": True}
        code, body, _ = self.request("POST", "/api/finish-cleanup", {})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["cleanup_recovered"])
        self.job.finish_cleanup.assert_called_once_with()

        self.job.request_stop.return_value = True
        self.assertEqual(self.request("POST", "/api/stop", {})[0], 202)
        self.job.request_stop.assert_called_once_with()

    def test_failed_connection_check_does_not_start_or_mutate(self) -> None:
        self.job.check_connection.return_value = {
            "ready": False,
            "checks": [{"id": "internet", "label": "Internet", "ok": False}],
            "error": "Tablet is offline",
        }
        code, body, _ = self.request("POST", "/api/connection-check", {})
        self.assertEqual(code, 409)
        self.assertEqual(json.loads(body)["error"], "Tablet is offline")
        self.job.start.assert_not_called()


class DashboardTokenTests(unittest.TestCase):
    def test_open_existing_reuses_private_token_without_starting_server(self) -> None:
        with (
            patch("sys.argv", ["khan-dashboard", "--open-existing", "--port", "8765"]),
            patch("dashboard.load_dashboard_token", return_value="T" * 43),
            patch("dashboard.webbrowser.open") as browser,
            redirect_stdout(io.StringIO()),
        ):
            main()
        browser.assert_called_once_with("http://127.0.0.1:8765/#" + "T" * 43)

    def test_token_is_private_stable_and_rotates_only_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private/dashboard-token.local"
            first = load_dashboard_token(path)
            second = load_dashboard_token(path)
            rotated = load_dashboard_token(path, rotate=True)

            self.assertEqual(first, second)
            self.assertNotEqual(first, rotated)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_or_public_token_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private/dashboard-token.local"
            path.parent.mkdir()
            path.write_text("not-a-valid-token\n")
            path.chmod(0o600)
            with self.assertRaisesRegex(AutomationError, "invalid"):
                load_dashboard_token(path)
            path.write_text("T" * 43 + "\n")
            path.chmod(0o644)
            with self.assertRaisesRegex(AutomationError, "owner-only"):
                load_dashboard_token(path)

    def test_two_server_instances_reuse_the_same_private_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = Mock(root=root)
            first = DashboardServer(0, job)
            token = first.token
            first.server_close()
            second = DashboardServer(0, job)
            try:
                self.assertEqual(second.token, token)
            finally:
                second.server_close()


class SyncJobTests(unittest.TestCase):
    def test_screen_pinning_cleanup_returns_home_without_replaying_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir()
            payload = {
                "student": "Student A",
                "run_id": "sync-pinned",
                "status": "no_op",
                "generated_at": "2026-09-19T15:24:31-04:00",
                "observed_assignments": [{"title": "Sounds", "variant": "Main"}],
                "desired_assignments": [{"title": "Sounds", "variant": "Main"}],
                "actions": [],
                "teardown": {
                    "status": "failed",
                    "kind": "lock_task_pinned",
                    "result_saved": True,
                    "error": "screen-pinned",
                },
            }
            plan = private / "student-a-reading-plan.json"
            plan.write_text(json.dumps(payload))
            job = SyncJob(root, root / "khan-mastery-sync", serial="synthetic-usb")
            job.state = "failed"
            job.student = "Student A"
            job.run_id = "sync-pinned"
            job.cancel_path = private / "dashboard-operations/sync-pinned.cancel"
            job.cancel_path.parent.mkdir()
            job.started_at = time.monotonic()
            job.report = build_dashboard_report(payload)
            device = Mock(spec=AndroidDevice)
            device.awake_session.return_value = nullcontext()
            device.is_locked.return_value = False

            with (
                patch.object(job, "_resolved_device", return_value=(device, "synthetic-usb")),
                patch(
                    "dashboard.tablet_access_health",
                    return_value={
                        "ready": True,
                        "checks": [{"id": "screen_pinning", "ok": True}],
                        "error": None,
                        "lock_task_mode": "none",
                    },
                ),
            ):
                result = job.finish_cleanup()

            self.assertTrue(result["cleanup_recovered"])
            device.return_to_android_home.assert_called_once_with("org.khankids.android")
            self.assertEqual(job.snapshot()["state"], "succeeded")
            self.assertEqual(json.loads(plan.read_text())["teardown"]["status"], "recovered")
            self.assertEqual(job.snapshot()["recovery_state"], "none")

    def test_manual_worker_uses_direct_session_without_sync_subprocess(self):
        assignment = {
            "grade": "Kindergarten",
            "title": "Lowercase l",
            "variant": "Main",
            "action": "assign",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("dashboard.CatalogIndex"),
            patch("dashboard.ManualAssignmentSession") as session,
            patch("dashboard.subprocess.Popen") as popen,
        ):
            root = Path(directory)
            job = SyncJob(root, root / "khan-mastery-sync", serial="synthetic-usb")
            session.return_value.__enter__.return_value.apply_many.return_value = [
                {
                    "student": "Student A",
                    "status": "applied",
                    "manual_change": assignment,
                    "queue_count": 1,
                    "assigned": [{"title": "Lowercase l", "variant": "Main"}],
                }
            ]
            self.assertTrue(job.start("Student A", assignment))
            job.wait()
            calls = session.return_value.__enter__.return_value.apply_many.call_args_list
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args[:2], ("Student A", assignment))
            self.assertIsNone(calls[0].args[2]())
            session.return_value.__enter__.return_value.park_at_android_home.assert_called_once()
            popen.assert_not_called()
            self.assertEqual(job.snapshot()["assignment_requests"][0]["state"], "succeeded")
            custom = SyncJob(root, root / "custom-workflow")
            with self.assertRaises(AutomationError):
                custom.start("Student A", assignment)

    def test_manual_success_requires_matching_verified_assignment_result(self):
        assignment = {
            "grade": "Kindergarten",
            "title": "Lowercase l",
            "variant": "Main",
            "action": "assign",
        }
        for matches in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                job = SyncJob(root, root / "khan-mastery-sync")
                report = {
                    "student": "Student A",
                    "status": "applied",
                    "manual_change": assignment if matches else None,
                    "queue_count": 1,
                    "assigned": [{"title": "Lowercase l", "variant": "Main"}],
                }
                with (
                    patch("dashboard.CatalogIndex"),
                    patch("dashboard.ManualAssignmentSession") as session,
                ):
                    session.return_value.__enter__.return_value.apply_many.return_value = [report]
                    self.assertTrue(job.start("Student A", assignment))
                    job.wait()
                self.assertEqual(job.snapshot()["state"], "succeeded" if matches else "failed")

    def test_json_report_is_structured_not_added_to_diagnostic_output(self) -> None:
        process = Mock()
        process.stdout = io.StringIO(
            "[progress] checking\n"
            + json.dumps(
                {
                    "dashboard_report": {
                        "student": "Student A",
                        "status": "no_op",
                        "run_id": "sync-dashboard-test",
                    }
                }
            )
            + "\n"
        )
        process.wait.return_value = 0
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("dashboard.subprocess.Popen") as popen,
        ):
            job = SyncJob(Path(directory), Path(directory) / "custom-wrapper")
            popen.return_value.__enter__.return_value = process
            job._run("Student A")
            self.assertEqual(job.snapshot()["report"]["status"], "no_op")
            self.assertNotIn("dashboard_report", job.snapshot()["output"])
            restarted = SyncJob(Path(directory), Path(directory) / "custom-wrapper")
            self.assertEqual(restarted.latest_report("Student A")["status"], "no_op")
            self.assertEqual(job._result_path("Student A").stat().st_mode & 0o777, 0o600)

    def test_normal_dashboard_run_persists_bounded_correlated_output(self) -> None:
        process = Mock()
        process.stdout = io.StringIO(
            "[progress] checking\n"
            + json.dumps(
                {
                    "dashboard_report": {
                        "student": "Student A",
                        "status": "no_op",
                        "run_id": "sync-dashboard-test",
                    }
                }
            )
            + "\n"
        )
        process.wait.return_value = 0
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("dashboard.subprocess.Popen") as popen,
            patch("dashboard.new_run_id", return_value="sync-dashboard-test"),
        ):
            root = Path(directory)
            job = SyncJob(root, root / "khan-mastery-sync")
            popen.return_value.__enter__.return_value = process
            self.assertTrue(job.start("Student A"))
            job.wait()

            run = root / "private/sync-runs/sync-dashboard-test"
            manifest = json.loads((run / "run.json").read_text())
            self.assertEqual(job.snapshot()["run_id"], "sync-dashboard-test")
            self.assertEqual(manifest["status"], "succeeded")
            self.assertEqual((run / "output.log").read_text(), "[progress] checking\n")
            self.assertIn("--run-id", popen.call_args.args[0])

    def test_latest_result_restores_saved_state_but_does_not_infer_custom_wrapper_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "private").mkdir()
            payload = {
                "student": "Student A",
                "status": "no_op",
                "observed_assignments": [{"title": "Sounds", "variant": "Main"}],
                "actions": [],
            }
            (root / "private/student-a-reading-plan.json").write_text(json.dumps(payload))
            job = SyncJob(root, root / "khan-mastery-sync")
            self.assertEqual(job.latest_report("Student A")["queue_count"], 1)
            self.assertIsNone(job.latest_report("Student B"))
            job.workflow = root / "custom-wrapper"
            self.assertIsNone(job.latest_report("Student A"))

    def test_command_reuses_wrapper_and_preserves_full_engine_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = SyncJob(root, root / "khan-mastery-sync", "USB_TEST_SERIAL")
            process = Mock()
            process.stdout = io.StringIO(
                "[progress] checking\n"
                + json.dumps(
                    {
                        "dashboard_report": {
                            "student": "Student A",
                            "status": "no_op",
                            "outcome": "verified",
                            "run_id": "sync-test-run",
                        }
                    }
                )
                + "\n"
            )
            process.wait.return_value = 0
            with (
                patch("dashboard.subprocess.Popen") as popen,
                patch("dashboard.new_run_id", return_value="sync-test-run"),
            ):
                popen.return_value.__enter__.return_value = process
                self.assertTrue(job.start("Student A"))
                job.wait()
            self.assertEqual(
                popen.call_args.args[0],
                [
                    str(root / "khan-mastery-sync"),
                    "--student",
                    "Student A",
                    "--json",
                    "--run-id",
                    "sync-test-run",
                    "--serial",
                    "USB_TEST_SERIAL",
                    "--cancel-file",
                    str(root / "private/dashboard-operations/sync-test-run.cancel"),
                ],
            )
            self.assertEqual(job.snapshot()["state"], "succeeded")
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(job.snapshot()["report"]["outcome"], "verified")

    def test_zero_exit_without_a_structured_result_never_claims_success(self) -> None:
        job = SyncJob(Path("/repo"), Path("/repo/command"))
        process = Mock(stdout=io.StringIO("Some unrelated output\n"))
        process.wait.return_value = 0
        with patch("dashboard.subprocess.Popen") as popen:
            popen.return_value.__enter__.return_value = process
            job._run("Student A")
        self.assertEqual(job.snapshot()["state"], "failed")
        self.assertIn("structured result", job.snapshot()["output"])

    def test_failure_never_reports_success(self) -> None:
        for code in (1, 2):
            job = SyncJob(Path("/repo"), Path("/repo/command"))
            process = Mock()
            process.stdout = io.StringIO("Startup blocked; app left open\n")
            process.wait.return_value = code
            with patch("dashboard.subprocess.Popen") as popen:
                popen.return_value.__enter__.return_value = process
                job._run("Student A")
            self.assertEqual(job.snapshot()["state"], "failed")
            self.assertEqual(job.snapshot()["returncode"], code)

    def test_process_failure_is_reported_without_raw_exception(self) -> None:
        job = SyncJob(Path("/repo"), Path("/repo/command"))
        with patch("dashboard.subprocess.Popen", side_effect=OSError("private details")):
            job._run("Student A")
        self.assertEqual(job.snapshot()["state"], "failed")
        self.assertNotIn("private details", job.snapshot()["output"])

    def test_concurrent_starts_are_rejected_and_wait_preserves_worker(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        job = SyncJob(root, root / "command")
        entered, release = threading.Event(), threading.Event()

        def run(student, assignment=None):
            entered.set()
            release.wait(5)

        with patch.object(job, "_run", side_effect=run):
            self.assertTrue(job.start("Student A"))
            self.assertTrue(entered.wait(5))
            self.assertFalse(job.start("Student A"))
            release.set()
            job.wait()
        self.assertFalse(job.thread.is_alive())

    def test_output_is_bounded(self) -> None:
        job = SyncJob(Path("/repo"), Path("/repo/command"))
        job._append("x" * (MAX_OUTPUT_CHARS + 100))
        self.assertEqual(len(job.snapshot()["output"]), MAX_OUTPUT_CHARS)

    def test_stop_request_is_durable_and_does_not_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = SyncJob(root, root / "khan-mastery-sync", stop_grace_seconds=0.01)
            job.state = "running"
            job.run_id = "sync-stop-test"
            job.student = "Student A"
            job.cancel_path = root / "private/dashboard-operations/sync-stop-test.cancel"
            job.cancel_path.parent.mkdir(parents=True)
            self.assertTrue(job.request_stop())
            self.assertEqual(job.snapshot()["state"], "stopping")
            self.assertIn("Stopped safely", job.cancel_path.read_text())
            self.assertFalse(job.start("Student A"))

    def test_watchdog_ignores_heartbeats_and_requests_safe_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = SyncJob(
                root,
                root / "khan-mastery-sync",
                stall_seconds=0.01,
                stop_grace_seconds=0.01,
            )
            job.state = "running"
            job.run_id = "sync-stall-test"
            job.student = "Student A"
            job.cancel_path = root / "private/dashboard-operations/sync-stall-test.cancel"
            job.cancel_path.parent.mkdir(parents=True)
            job.last_progress_at = 0
            job._append("[progress] Still working; waiting for the current UI operation\n")
            watcher = threading.Thread(target=job._watch_for_stall, args=(threading.Event(),))
            watcher.start()
            watcher.join(2)
            self.assertFalse(watcher.is_alive())
            self.assertEqual(job.snapshot()["state"], "stopping")
            self.assertTrue(job.cancel_path.exists())

    def test_active_operation_survives_dashboard_restart_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "private/dashboard-active-operation.json"
            active.parent.mkdir(parents=True)
            cancel = root / "private/dashboard-operations/run.cancel"
            cancel.parent.mkdir()
            active.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "student": "Student A",
                        "run_id": "sync-restart-test",
                        "pid": 12345,
                        "cancel_path": str(cancel),
                        "started_at": 1,
                    }
                )
            )
            active.chmod(0o600)
            with patch.object(SyncJob, "_refresh_external_locked"):
                job = SyncJob(root, root / "khan-mastery-sync")
                self.assertEqual(job.snapshot()["state"], "recovering")
                self.assertFalse(job.start("Student A"))

    def test_dead_recovered_operation_cannot_claim_an_older_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            operations = private / "dashboard-operations"
            operations.mkdir(parents=True)
            (private / "student-a-reading-plan.json").write_text(
                json.dumps(
                    {
                        "student": "Student A",
                        "status": "no_op",
                        "run_id": "older-run",
                        "generated_at": "2026-09-19T12:00:00-04:00",
                        "observed_assignments": [],
                        "actions": [],
                    }
                )
            )
            active = private / "dashboard-active-operation.json"
            active.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "student": "Student A",
                        "run_id": "interrupted-run",
                        "pid": 999_999_999,
                        "cancel_path": str(operations / "interrupted-run.cancel"),
                        "started_at": time.time(),
                    }
                )
            )
            active.chmod(0o600)
            job = SyncJob(root, root / "khan-mastery-sync")
            self.assertEqual(job.snapshot()["state"], "failed")
            self.assertEqual(job.snapshot()["recovery_state"], "connection_check_required")

    def test_latest_interrupted_result_survives_clean_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir()
            (private / "student-a-reading-plan.json").write_text(
                json.dumps(
                    {
                        "student": "Student A",
                        "status": "interrupted",
                        "run_id": "interrupted-run",
                        "generated_at": "2026-09-19T12:00:00-04:00",
                        "observed_assignments": [],
                        "actions": [],
                        "applied": [{"action": "unchecked", "title": "A", "variant": "Main"}],
                        "recovery": {"status": "unavailable"},
                    }
                )
            )
            with (
                patch("dashboard.DeviceConfig.load") as config,
                patch("dashboard.load_aliases", return_value={"Local reader": "Student A"}),
            ):
                config.return_value.student = "Local reader"
                job = SyncJob(root, root / "khan-mastery-sync")
            self.assertEqual(job.snapshot()["state"], "failed")
            self.assertEqual(job.snapshot()["run_id"], "interrupted-run")
            self.assertEqual(job.snapshot()["recovery_state"], "review_required")

    def test_setup_uses_existing_private_readers_and_never_returns_credentials(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("dashboard.load_aliases", return_value={"LOCAL_NAME_PLACEHOLDER": "Student A"}),
            patch("dashboard.read_local_secrets") as credentials,
            patch("dashboard.DeviceConfig.load") as config,
            patch("dashboard.shutil.which", return_value="installed"),
            patch("dashboard.importlib.util.find_spec", return_value=object()),
        ):
            config.return_value.student = "Student A"
            data = setup_status(Path(directory), None)
            credentials.assert_called_once_with(Path(directory) / ".secrets.json")
            self.assertTrue(data["ready"])
            self.assertEqual(data["default_student"], "Student A")
            self.assertEqual(data["students"], ["Student A"])
            self.assertEqual(data["display_names"], {"Student A": "LOCAL_NAME_PLACEHOLDER"})

    def test_missing_setup_disables_sync_but_serial_avoids_device_config_requirement(self) -> None:
        from khan_kids.device_discovery import DeviceDiscoveryError

        with (
            patch("dashboard.load_aliases", return_value={}),
            patch("dashboard.read_local_secrets", side_effect=OSError()),
            patch("dashboard.DeviceConfig.load", side_effect=DeviceDiscoveryError("missing")),
        ):
            data = setup_status(Path("/missing"), "USB_TEST_SERIAL")
        self.assertFalse(data["ready"])
        self.assertTrue(data["checks"][2]["ok"])


if __name__ == "__main__":
    unittest.main()
