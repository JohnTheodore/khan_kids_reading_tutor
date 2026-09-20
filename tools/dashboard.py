#!/usr/bin/env python3
"""Serve a loopback-only interface to the existing mastery sync command."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import threading
import time
import webbrowser
from contextlib import suppress
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from khan_kids.adb import AndroidDevice, AutomationError
from khan_kids.catalog import CatalogIndex
from khan_kids.checkin_history import checkin_history
from khan_kids.constants import KHAN_KIDS_PACKAGE
from khan_kids.device_discovery import DeviceConfig, DeviceDiscoveryError, resolve_device
from khan_kids.diagnostics import DiagnosticRun, new_run_id
from khan_kids.incidents import append_failed_sync_incident
from khan_kids.launcher import read_local_secrets
from khan_kids.manual_assignments import ManualChange
from khan_kids.manual_session import ManualAssignmentSession
from khan_kids.preflight import tablet_access_health, tablet_health
from khan_kids.reading_journey import family_journeys
from khan_kids.records import write_json_atomic, write_text_atomic
from khan_kids.student_identity import load_aliases
from khan_kids.sync_report import build_dashboard_report

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tools" / "dashboard_assets"
MAX_OUTPUT_CHARS = 250_000
PARENT_IDLE_SECONDS = 60
PARENT_COALESCE_SECONDS = 0.5
MAX_PARENT_REQUESTS = 50
SYNC_STALL_SECONDS = 90
STOP_GRACE_SECONDS = 30
DASHBOARD_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def load_dashboard_token(path: Path, *, rotate: bool = False) -> str:
    """Load one owner-private token that survives ordinary service restarts."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and not rotate:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise AutomationError("Dashboard token must be a regular owner-private file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise AutomationError("Dashboard token requires owner-only permissions")
        raw = path.read_text()
        token = raw[:-1] if raw.endswith("\n") else raw
        if raw not in {token, token + "\n"} or not DASHBOARD_TOKEN_PATTERN.fullmatch(token):
            raise AutomationError("Dashboard token file is invalid")
        return token
    token = secrets.token_urlsafe(32)
    write_text_atomic(path, token + "\n")
    path.chmod(0o600)
    return token


def sync_progress(output: str, state: str) -> float:
    """Stage completion, not a time estimate; failures never imply completion."""
    if state == "succeeded":
        return 1.0
    checkpoints = {
        "Starting phase.review_assignments": 0.2,
        "Finished phase.review_assignments": 0.4,
        "Finished phase.plan_queue": 0.6,
        "Finished phase.fixed_point_verify": 0.8,
        "Starting phase.inspect_parent_assignment": 0.2,
        "Finished phase.inspect_parent_assignment": 0.4,
        "Finished phase.save_parent_assignment": 0.6,
        "Finished phase.verify_parent_assignment": 0.8,
        "Starting teardown.": 0.8,
        "Finished teardown.": 0.9,
    }
    return max((value for marker, value in checkpoints.items() if marker in output), default=0.0)


def reading_profiles(root: Path, students: list[str]) -> dict:
    """Trusted owner-only local configuration; browser requests cannot set paths."""
    config = root / "private/dashboard-profiles.local.json"
    if not config.exists():
        return {}
    if config.stat().st_mode & 0o077:
        raise ValueError("Private reading profiles require owner-only permissions")
    profiles = json.loads(config.read_text())
    if not isinstance(profiles, dict) or any(
        student not in students or not isinstance(profile, dict)
        for student, profile in profiles.items()
    ):
        raise ValueError("Invalid reading profiles")
    for profile in profiles.values():
        if (
            set(profile) - {"attempts", "format", "archived", "captured_on"}
            or ("attempts" in profile and not isinstance(profile["attempts"], str))
            or not isinstance(profile.get("format", "native"), str)
            or profile.get("format", "native") not in {"native", "archive"}
            or not isinstance(profile.get("archived", False), bool)
            or ("captured_on" in profile and not isinstance(profile["captured_on"], str))
            or (profile.get("format") == "archive" and not profile.get("archived"))
        ):
            raise ValueError("Invalid reading profile fields")
    return profiles


def setup_status(root: Path, serial: str | None) -> dict:
    """Inspect local setup without connecting to the tablet or returning secrets."""
    checks = []
    students = []
    display_names = {}
    default = None
    with suppress(ValueError, OSError):
        aliases = load_aliases()
        students = sorted(set(aliases.values()))
        display_names = {alias: name for name, alias in aliases.items()}
    checks.append({"label": "Private student aliases", "ok": bool(students)})
    try:
        read_local_secrets(root / ".secrets.json")
        credentials = True
    except Exception:
        credentials = False
    checks.append({"label": "Credentials present with owner-only permissions", "ok": credentials})
    try:
        config = DeviceConfig.load(root / "private/tablet-device.local.json")
        default = config.student if config.student in students else None
        device = True
    except DeviceDiscoveryError:
        device = False
    checks.append(
        {
            "label": "Tablet configured (or explicit USB serial supplied)",
            "ok": device or bool(serial),
        }
    )
    adb = (root / "private/android-sdk/platform-tools/adb").is_file() or bool(shutil.which("adb"))
    checks.extend(
        [
            {"label": "ADB installed", "ok": adb},
            {
                "label": "ImageMagick installed",
                "ok": bool(shutil.which("magick") or shutil.which("convert")),
            },
            {
                "label": "Python UI automation installed",
                "ok": importlib.util.find_spec("uiautomator2") is not None,
            },
        ]
    )
    archived_students = []
    try:
        profiles = reading_profiles(root, students)
        archived_students = [
            student for student, profile in profiles.items() if profile.get("archived")
        ]
    except (OSError, ValueError):
        checks.append({"label": "Private reading profiles valid and owner-only", "ok": False})
    return {
        "checks": checks,
        "students": students,
        "display_names": display_names,
        "archived_students": archived_students,
        "default_student": default,
        "ready": all(check["ok"] for check in checks),
        "connection": "explicit wired/device serial" if serial else "configured ADB discovery",
    }


class SyncJob:
    """Serialize tablet work; parent edits use a reusable, independent session."""

    def __init__(
        self,
        root: Path,
        workflow: Path,
        serial: str | None = None,
        *,
        debug: bool = False,
        stall_seconds: float = SYNC_STALL_SECONDS,
        stop_grace_seconds: float = STOP_GRACE_SECONDS,
    ) -> None:
        self.root, self.workflow, self.serial = root, workflow, serial
        self.debug = debug
        self.debug_path = root / "private/dashboard-debug/events.jsonl"
        if debug:
            self.debug_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.debug_path.touch(mode=0o600)
            if self.debug_path.stat().st_mode & 0o077:
                raise AutomationError("Debug log requires owner-only permissions")
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.thread: threading.Thread | None = None
        self.state = "idle"
        self.output = ""
        self.returncode: int | None = None
        self.report: dict | None = None
        self.student: str | None = None
        self.assignment: dict | None = None
        self.started_at: float | None = None
        self.run_id: str | None = None
        self.diagnostic: DiagnosticRun | None = None
        self.manual_worker = False
        self.manual_batch_size = 0
        self.warm_deadline: float | None = None
        self.stopping = False
        self.stop_requested = False
        self.stop_reason: str | None = None
        self.last_progress_at: float | None = None
        self.stall_seconds = stall_seconds
        self.stop_grace_seconds = stop_grace_seconds
        self.process: subprocess.Popen | None = None
        self.external_pid: int | None = None
        self.operation_path = root / "private/dashboard-active-operation.json"
        self.cancel_path: Path | None = None
        self.requests_path = root / "private/dashboard-assignment-queue.json"
        self.requests = []
        if (
            self.workflow.resolve() == (self.root / "khan-mastery-sync").resolve()
            and self.requests_path.exists()
        ):
            if self.requests_path.stat().st_mode & 0o077:
                raise AutomationError("Assignment queue requires owner-only permissions")
            self.requests = json.loads(self.requests_path.read_text())
            if not isinstance(self.requests, list) or any(
                not isinstance(request, dict)
                or set(request) != {"id", "student", "assignment", "state", "error"}
                or not isinstance(request.get("id"), str)
                or not isinstance(request.get("student"), str)
                or not isinstance(request.get("assignment"), dict)
                or set(request["assignment"]) != {"grade", "title", "variant", "action"}
                or any(not isinstance(value, str) for value in request["assignment"].values())
                or request["assignment"]["action"] not in {"assign", "unassign"}
                or (request.get("error") is not None and not isinstance(request["error"], str))
                or request.get("state")
                not in {"queued", "running", "succeeded", "failed", "blocked"}
                for request in self.requests
            ):
                raise AutomationError("Saved assignment queue could not be validated")
            for request in self.requests:
                if request["state"] in {"queued", "running"}:
                    request.update(
                        state="blocked",
                        error="Dashboard restarted. Check the tablet before explicitly trying this request again; it was not automatically replayed.",
                    )
            self._save_requests()
        self._restore_operation()
        self._restore_latest_failure()

    def _restore_operation(self) -> None:
        """Restore awareness of an uncompleted sync without replaying it."""
        try:
            if not self.operation_path.exists():
                return
            if self.operation_path.stat().st_mode & 0o077:
                raise AutomationError("Active dashboard operation requires owner-only permissions")
            operation = json.loads(self.operation_path.read_text())
            if (
                not isinstance(operation, dict)
                or not isinstance(operation.get("student"), str)
                or not isinstance(operation.get("run_id"), str)
                or not isinstance(operation.get("cancel_path"), str)
                or not isinstance(operation.get("started_at"), (int, float))
            ):
                raise AutomationError("Active dashboard operation could not be validated")
            self.student = operation["student"]
            self.run_id = operation["run_id"]
            self.cancel_path = Path(operation["cancel_path"])
            operations_root = (self.root / "private/dashboard-operations").resolve()
            if not self.cancel_path.resolve().is_relative_to(operations_root):
                raise AutomationError("Saved cancellation path is outside private operations")
            self.started_at = time.monotonic() - max(
                0, time.time() - float(operation["started_at"])
            )
            self.last_progress_at = time.monotonic()
            saved_state = operation.get("state")
            if saved_state in {"succeeded", "failed"}:
                self.report = self.latest_report(self.student)
                self.state = (
                    saved_state
                    if self.report and self.report.get("run_id") == self.run_id
                    else "failed"
                )
                return
            if not isinstance(operation.get("pid"), int):
                raise AutomationError("Active dashboard operation has no process identity")
            self.external_pid = operation["pid"]
            self.state = "recovering"
            self._refresh_external_locked()
        except (OSError, ValueError, TypeError, AutomationError):
            self.state = "failed"
            self.stop_reason = "A previous sync state could not be validated. Check the connection before retrying."

    def _restore_latest_failure(self) -> None:
        """Surface the latest interrupted native result after a clean service restart."""
        if (
            self.state != "idle"
            or self.workflow.resolve() != (self.root / "khan-mastery-sync").resolve()
        ):
            return
        try:
            configured = DeviceConfig.load(self.root / "private/tablet-device.local.json").student
            student = load_aliases().get(configured, configured)
            report = self.latest_report(student)
        except (DeviceDiscoveryError, OSError, ValueError):
            return
        if report and report.get("status") == "interrupted" and report.get("run_id"):
            self.student = student
            self.run_id = str(report["run_id"])
            self.report = report
            self.state = "failed"

    def _operation_record(self, *, pid: int, state: str) -> dict[str, object]:
        assert self.student and self.run_id and self.cancel_path
        return {
            "version": 1,
            "state": state,
            "student": self.student,
            "run_id": self.run_id,
            "pid": pid,
            "cancel_path": str(self.cancel_path.resolve()),
            "started_at": time.time() - (time.monotonic() - (self.started_at or time.monotonic())),
        }

    def _persist_operation(self, pid: int) -> None:
        write_json_atomic(self.operation_path, self._operation_record(pid=pid, state="running"))

    def _persist_finished_operation(self) -> None:
        if self.cancel_path is None or self.run_id is None or self.student is None:
            return
        write_json_atomic(
            self.operation_path,
            self._operation_record(pid=0, state=self.state),
        )

    def _refresh_external_locked(self) -> None:
        if self.state not in {"recovering", "stopping"} or self.external_pid is None:
            return
        try:
            os.kill(self.external_pid, 0)
            command = Path(f"/proc/{self.external_pid}/cmdline").read_bytes()
            if self.run_id.encode() in command and (
                self.workflow.name.encode() in command
                or (
                    self.workflow.resolve() == (self.root / "khan-mastery-sync").resolve()
                    and b"reading_workflow.py" in command
                )
            ):
                return
        except (OSError, ProcessLookupError):
            pass
        self.external_pid = None
        self.report = self.latest_report(self.student) if self.student else None
        self.state = (
            "succeeded"
            if self.report
            and self.report.get("run_id") == self.run_id
            and self.report.get("status") in {"applied", "no_op", "review_required"}
            else "failed"
        )
        with suppress(OSError):
            self._persist_finished_operation()

    def check_connection(self) -> dict[str, object]:
        """Inspect the tablet without opening Khan Kids, repairing screen pinning if needed."""
        try:
            device, serial = self._resolved_device()
            result = tablet_health(device)
            result["transport"] = "USB" if ":" not in serial else "wireless ADB"
            return result
        except (AutomationError, DeviceDiscoveryError, OSError) as error:
            return {"ready": False, "checks": [], "error": str(error)}

    def finish_cleanup(self) -> dict[str, object]:
        """Finish only a saved sync's screen-pinning cleanup; never replay the sync."""
        with self.lock:
            if self._recovery_state() != "cleanup_required" or not self.student or not self.run_id:
                return {"ready": False, "checks": [], "error": "No saved cleanup is pending"}
            student, run_id = self.student, self.run_id
            self.state = "recovering"
        try:
            device, serial = self._resolved_device()
            access = tablet_access_health(device)
            access["transport"] = "USB" if ":" not in serial else "wireless ADB"
            if not access["ready"]:
                return access
            device.return_to_android_home(KHAN_KIDS_PACKAGE)
            plan_path = (
                self.root / "private" / f"{student.casefold().replace(' ', '-')}-reading-plan.json"
            )
            payload = json.loads(plan_path.read_text())
            teardown = payload.get("teardown") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or payload.get("student") != student
                or payload.get("run_id") != run_id
                or not isinstance(teardown, dict)
                or teardown.get("status") != "failed"
            ):
                raise AutomationError("Saved cleanup result no longer matches this operation")
            teardown.update(
                status="recovered",
                recovered_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            write_json_atomic(plan_path, payload)
            report = build_dashboard_report(payload)
            with self.lock:
                self.report = report
                self.state = "succeeded"
                self.returncode = 0
                self._persist_finished_operation()
            return {**access, "ready": True, "cleanup_recovered": True}
        except (AutomationError, DeviceDiscoveryError, OSError, ValueError, TypeError) as error:
            return {"ready": False, "checks": [], "error": str(error)}
        finally:
            with self.lock:
                if self.state == "recovering":
                    self.state = "failed"

    def _resolved_device(self) -> tuple[AndroidDevice, str]:
        serial = self.serial
        if not serial:
            config = DeviceConfig.load(self.root / "private/tablet-device.local.json")
            serial = resolve_device(config)
        return AndroidDevice(serial), serial

    def _save_requests(self) -> None:
        write_json_atomic(self.requests_path, self.requests)
        self._debug_event("queue", requests=self.requests)

    def _debug_event(self, event: str, **details) -> None:
        if self.debug:
            try:
                with self.debug_path.open("a") as output:
                    output.write(
                        json.dumps(
                            {
                                "time": datetime.now().astimezone().isoformat(),
                                "event": event,
                                **details,
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass  # Observability must never interrupt an already saved action.

    def _manual_progress(self, message: str) -> None:
        self._append(message + "\n")

    def snapshot(self) -> dict:
        with self.lock:
            self._refresh_external_locked()
            fraction = sync_progress(self.output, self.state)
            if self.manual_worker and self.state != "succeeded" and self.manual_batch_size:
                verified = self.output.count("Finished phase.verify_parent_checkbox")
                saved = self.output.count("Finished phase.save_parent_assignment")
                # Completed observed work, with a reserved final queue check.
                fraction = min(
                    0.9, (0.8 * verified + 0.4 * max(0, saved - verified)) / self.manual_batch_size
                )
                if "Finished phase.verify_parent_assignment" in self.output:
                    fraction = max(fraction, 0.9)
            return {
                "state": self.state,
                "output": self.output,
                "returncode": self.returncode,
                "report": self.report,
                "student": self.student,
                "assignment": self.assignment,
                "progress_fraction": fraction,
                "elapsed_seconds": time.monotonic() - self.started_at if self.started_at else None,
                "run_id": self.run_id,
                "recovery_state": self._recovery_state(),
                "stop_reason": self.stop_reason,
                "seconds_since_progress": round(time.monotonic() - self.last_progress_at, 1)
                if self.last_progress_at and self.state in {"running", "stopping", "recovering"}
                else None,
                "assignment_requests": [dict(request) for request in self.requests],
                "teacher_session": "warm"
                if self.warm_deadline is not None
                else "active"
                if self.manual_worker
                else "closed",
                "teacher_idle_seconds": max(0, int(self.warm_deadline - time.monotonic()))
                if self.warm_deadline is not None
                else None,
            }

    def _recovery_state(self) -> str:
        if self.state == "stopping":
            return "stopping_safely"
        if self.state == "recovering":
            return "operation_continues"
        if self.state != "failed":
            return "none"
        if self.report and self.report.get("status") == "interrupted":
            return "safe_to_retry" if not self.report.get("applied") else "review_required"
        teardown = self.report.get("teardown") if self.report else None
        if (
            isinstance(teardown, dict)
            and teardown.get("status") == "failed"
            and teardown.get("result_saved") is True
            and teardown.get("kind") == "lock_task_pinned"
        ):
            return "cleanup_required"
        return "connection_check_required"

    def journeys(self, students: list[str]) -> dict:
        """Only the native workflow may infer native record locations."""
        try:
            profiles = reading_profiles(self.root, students)
        except (OSError, ValueError):
            return {
                "readers": [],
                "error": "Private reading profiles could not be validated. Check their contents and owner-only permissions.",
            }
        if self.workflow.resolve() != (self.root / "khan-mastery-sync").resolve():
            return {
                "readers": [],
                "error": "Reading analytics require the native workflow; custom engines retain their isolated sync reports.",
            }
        return family_journeys(
            self.root,
            students,
            profiles=profiles,
            reports={student: self.latest_report(student) for student in students},
        )

    def start(self, student: str, assignment: dict | None = None) -> bool:
        if assignment:
            if self.workflow.resolve() != (self.root / "khan-mastery-sync").resolve():
                raise AutomationError("Lesson assignment requires the native workflow")
            assignment = ManualChange.from_dict(
                assignment, CatalogIndex(self.root / "data/reading-ela-archive.json")
            ).as_dict()
        with self.lock:
            if self.stopping:
                return False
            if assignment:
                pending = [
                    request
                    for request in self.requests
                    if request["student"] == student
                    and all(
                        request["assignment"][key] == assignment[key]
                        for key in ("grade", "title", "variant")
                    )
                    and request["state"] in {"queued", "running"}
                ]
                if pending and pending[-1]["assignment"] == assignment:
                    return True
                superseded = [request for request in pending if request["state"] == "queued"]
                if (
                    sum(request["state"] in {"queued", "running"} for request in self.requests)
                    - len(superseded)
                    >= MAX_PARENT_REQUESTS
                ):
                    return False
                previous = self.requests
                superseded_ids = {request["id"] for request in superseded}
                # Copy only pending entries being replaced; keep running request
                # identities intact if durable queue storage fails.
                self.requests = [
                    {
                        **request,
                        "state": "blocked",
                        "error": "Not applied: replaced by your newer request for this lesson.",
                    }
                    if request["id"] in superseded_ids
                    else request
                    for request in self.requests
                ]
                request = {
                    "id": secrets.token_hex(12),
                    "student": student,
                    "assignment": assignment,
                    "state": "queued",
                    "error": None,
                }
                kept = set(
                    [r["id"] for r in self.requests if r["state"] not in {"queued", "running"}][
                        -50:
                    ]
                )
                self.requests = [
                    r
                    for r in self.requests
                    if r["state"] in {"queued", "running"} or r["id"] in kept
                ] + [request]
                try:
                    self._save_requests()
                except OSError:
                    self.requests = previous
                    raise
                if not self.thread or not self.thread.is_alive():
                    self._start_manual_locked()
                self.condition.notify_all()
                return True
            if self.state in {"running", "stopping", "recovering"}:
                return False
            if self.manual_worker:
                return False
            self.state, self.output, self.returncode = "running", "", None
            self.report, self.student = None, student
            self.run_id = new_run_id()
            self.diagnostic = None
            with suppress(OSError):
                self.diagnostic = DiagnosticRun(self.root, self.run_id, student=student)
            self.assignment = assignment
            self.started_at = time.monotonic()
            self.last_progress_at = self.started_at
            self.stop_requested = False
            self.stop_reason = None
            operation_dir = self.root / "private/dashboard-operations"
            operation_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.cancel_path = operation_dir / f"{self.run_id}.cancel"
            with suppress(FileNotFoundError):
                self.cancel_path.unlink()
            self.thread = threading.Thread(target=self._run, args=(student,))
            self.thread.start()
        return True

    def _start_manual_locked(self) -> None:
        queued = next(r for r in self.requests if r["state"] == "queued")
        self.student, self.assignment = queued["student"], queued["assignment"]
        self.run_id, self.diagnostic = None, None
        self.manual_worker = True
        self.state = "running"
        self.warm_deadline = None
        self.thread = threading.Thread(target=self._run_manual)
        self.thread.start()

    def _run_manual(self) -> None:
        current = None
        batch = []
        session = None
        try:
            with self.condition:
                self.output, self.report = "", None
                self.started_at = time.monotonic()
                self.condition.wait_for(lambda: self.stopping, timeout=PARENT_COALESCE_SECONDS)
            catalog = CatalogIndex(self.root / "data/reading-ela-archive.json")

            def position(request):
                assignment = request["assignment"]
                return (
                    catalog.order_key(assignment["grade"], assignment["title"]),
                    assignment["variant"],
                )

            def take_next():
                nonlocal current
                with self.condition:
                    candidates = [
                        r
                        for r in self.requests
                        if r["state"] == "queued"
                        and r["student"] == batch[0]["student"]
                        and r["assignment"]["grade"] == batch[0]["assignment"]["grade"]
                        and position(r) >= position(batch[-1])
                        and not any(
                            (r["assignment"]["title"], r["assignment"]["variant"])
                            == (done["assignment"]["title"], done["assignment"]["variant"])
                            for done in batch
                        )
                    ]
                    if not candidates or len(batch) >= MAX_PARENT_REQUESTS:
                        return None
                    self.manual_batch_size = len(batch) + len(candidates)
                    current = min(candidates, key=position)
                    current["state"] = "running"
                    batch.append(current)
                    self.assignment = current["assignment"]
                    self._save_requests()
                    return current["assignment"]

            with ManualAssignmentSession(
                self.root, self.serial, self._manual_progress, debug=self.debug
            ) as session:
                while True:
                    with self.condition:
                        queued = next((r for r in self.requests if r["state"] == "queued"), None)
                        if queued is None:
                            if self.warm_deadline is None:
                                self.warm_deadline = time.monotonic() + PARENT_IDLE_SECONDS
                            remaining = self.warm_deadline - time.monotonic()
                            if remaining <= 0 or self.stopping:
                                self.warm_deadline = None
                                break
                            self.condition.wait(timeout=remaining)
                            continue
                        current = queued
                        # Only reorder independent requests within this student's grade.
                        compatible = [
                            r
                            for r in self.requests
                            if r["state"] == "queued"
                            and r["student"] == current["student"]
                            and r["assignment"]["grade"] == current["assignment"]["grade"]
                        ]
                        current = min(compatible, key=position)
                        batch = [current]
                        self.manual_batch_size = len(compatible)
                        current["state"] = "running"
                        self.state, self.output, self.returncode = "running", "", None
                        self.student, self.assignment = current["student"], current["assignment"]
                        self.report = None
                        self.warm_deadline = None
                        self.started_at = time.monotonic()
                        self._save_requests()
                    reports = session.apply_many(
                        current["student"], current["assignment"], take_next
                    )
                    if len(reports) != len(batch) or any(
                        not self._valid_assignment_report(
                            report, request["student"], request["assignment"]
                        )
                        for request, report in zip(batch, reports, strict=True)
                    ):
                        raise AutomationError(
                            "The parent batch did not return exact verified assignment results"
                        )
                    with self.condition:
                        self.report = reports[-1]
                        for request in batch:
                            request["state"] = "succeeded"
                        self.state, self.returncode = "succeeded", 0
                        self._save_requests()
                        self.warm_deadline = time.monotonic() + PARENT_IDLE_SECONDS
                        self.condition.notify_all()
                    current = None
        except Exception as error:
            self._debug_event("failure", kind=type(error).__name__, message=str(error))
            self._append("Parent assignment session stopped. Check the tablet before retrying.\n")
            with self.condition:
                for request in batch:
                    recovered = next(
                        (
                            report
                            for report in getattr(session, "verified_reports", [])
                            if self._valid_assignment_report(
                                report, request["student"], request["assignment"]
                            )
                        ),
                        None,
                    )
                    request.update(
                        state="failed",
                        error="Assignment could not be verified. Check the tablet before retrying.",
                    )
                    if recovered is not None:
                        request.update(state="succeeded", error=None)
                        self.report = recovered
                for request in self.requests:
                    if request["state"] == "queued":
                        request.update(
                            state="blocked",
                            error="Not applied: an earlier tablet operation failed. Check the tablet before retrying.",
                        )
                self.state, self.returncode = "failed", 1
                self.warm_deadline = None
                with suppress(OSError):
                    self._save_requests()
            with suppress(Exception):
                journal = (
                    self.root
                    / f"private/{(self.student or 'unknown').casefold().replace(' ', '-')}-manual-operation.json"
                )
                payload = json.loads(journal.read_text()) if journal.exists() else None
                append_failed_sync_incident(
                    self.root / "INCIDENTS.md",
                    student=self.student or "unknown",
                    error=error,
                    payload=payload,
                )
        finally:
            with self.condition:
                self.manual_worker = False
                self.manual_batch_size = 0
                self.warm_deadline = None
                # Requests received during the bounded logout transition start a new
                # session only after the previous lock/awake context is released.
                if any(r["state"] == "queued" for r in self.requests):
                    self._start_manual_locked()

    @staticmethod
    def _valid_assignment_report(report: dict, student: str, assignment: dict) -> bool:
        return bool(
            report
            and report.get("student") == student
            and report.get("manual_change") == assignment
            and report.get("status") in {"applied", "no_op"}
            and report.get("queue_count") is not None
            and any(
                a.get("title") == assignment["title"] and a.get("variant") == assignment["variant"]
                for a in report.get("assigned", [])
            )
            == (assignment["action"] == "assign")
        )

    def _append(self, text: str) -> None:
        with self.lock:
            self.output = (self.output + text)[-MAX_OUTPUT_CHARS:]
            if "Still working; waiting for the current UI operation" not in text:
                self.last_progress_at = time.monotonic()
            if self.diagnostic is not None:
                with suppress(OSError):
                    self.diagnostic.append_output(text)
            self._debug_event("progress", message=text.rstrip())

    def _run(self, student: str) -> None:
        command = [str(self.workflow), "--student", student, "--json"]
        if self.run_id and self.workflow.resolve() == (self.root / "khan-mastery-sync").resolve():
            command.extend(["--run-id", self.run_id])
        if self.serial:
            command.extend(["--serial", self.serial])
        if self.cancel_path:
            command.extend(["--cancel-file", str(self.cancel_path)])
        code = 1
        watchdog_finished = threading.Event()
        try:
            with subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                start_new_session=True,
            ) as process:
                with self.lock:
                    self.process = process
                    pid = getattr(process, "pid", None)
                    if isinstance(pid, int):
                        self._persist_operation(pid)
                watchdog = threading.Thread(
                    target=self._watch_for_stall,
                    args=(watchdog_finished,),
                    daemon=True,
                )
                watchdog.start()
                assert process.stdout is not None
                for line in process.stdout:
                    with suppress(ValueError):
                        data = json.loads(line)
                        if isinstance(data, dict) and isinstance(
                            data.get("dashboard_report"), dict
                        ):
                            with self.lock:
                                self.report = data["dashboard_report"]
                            continue
                    self._append(line)
                code = process.wait()
                watchdog_finished.set()
                if code == 0 and (
                    not self.report
                    or self.report.get("student") != student
                    or self.report.get("status") not in {"applied", "no_op", "review_required"}
                    or (
                        self.workflow.resolve() == (self.root / "khan-mastery-sync").resolve()
                        and self.report.get("run_id") != self.run_id
                    )
                ):
                    code = 1
                    self._append(
                        "The command did not return a valid structured result. Check that your sync launcher forwards --json.\n"
                    )
        except Exception:
            self._append("Unable to run sync command. Check the local workflow and installation.\n")
        finally:
            watchdog_finished.set()
            if self.report and self.report.get("student") == student:
                try:
                    destination = self._result_path(student)
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    write_json_atomic(destination, self.report)
                except OSError:
                    self._append(
                        "Reading results could not be cached; engine records are unchanged.\n"
                    )
            with self.lock:
                self.process = None
                self.external_pid = None
                self.returncode = code
                self.state = "succeeded" if code == 0 else "failed"
                with suppress(OSError):
                    self._persist_finished_operation()
                if self.cancel_path:
                    with suppress(OSError):
                        self.cancel_path.unlink()
                if self.diagnostic is not None:
                    with suppress(OSError):
                        self.diagnostic.finish(self.state, returncode=code)
                if any(r["state"] == "queued" for r in self.requests):
                    if code == 0:
                        self._start_manual_locked()
                    else:
                        for request in self.requests:
                            if request["state"] == "queued":
                                request.update(
                                    state="blocked",
                                    error="Not applied: mastery sync failed. Check the tablet before retrying.",
                                )
                        self._save_requests()

    def _watch_for_stall(self, finished: threading.Event) -> None:
        while not finished.wait(1):
            with self.lock:
                if self.state != "running" or self.last_progress_at is None:
                    continue
                if time.monotonic() - self.last_progress_at < self.stall_seconds:
                    continue
                self._request_stop_locked(
                    "No verified progress was observed within the safety window."
                )
                process = self.process
            if finished.wait(self.stop_grace_seconds):
                return
            if process is not None and isinstance(getattr(process, "pid", None), int):
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGINT)
            return

    def request_stop(self, reason: str = "Stopped safely from the dashboard.") -> bool:
        with self.condition:
            if self.state not in {"running", "recovering", "stopping"} or self.manual_worker:
                return False
            self._request_stop_locked(reason)
            pid = (
                getattr(self.process, "pid", None)
                if self.process is not None
                else self.external_pid
            )
            self.condition.notify_all()
        if isinstance(pid, int):
            threading.Thread(target=self._interrupt_after_grace, args=(pid,), daemon=True).start()
        return True

    def _request_stop_locked(self, reason: str) -> None:
        if self.stop_requested:
            return
        self.stop_requested = True
        self.stop_reason = reason
        self.state = "stopping"
        if self.cancel_path is not None:
            write_text_atomic(self.cancel_path, reason + "\n")

    def _interrupt_after_grace(self, pid: int) -> None:
        time.sleep(self.stop_grace_seconds)
        with self.lock:
            active = self.state == "stopping" and (
                self.external_pid == pid
                or (self.process is not None and getattr(self.process, "pid", None) == pid)
            )
        if active:
            with suppress(Exception):
                append_failed_sync_incident(
                    self.root / "INCIDENTS.md",
                    student=self.student or "unknown",
                    error=AutomationError(
                        "Safe stop exceeded its grace period; the workflow was interrupted"
                    ),
                    run_id=self.run_id,
                )
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGINT)

    def wait(self) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        while self.thread:
            thread = self.thread
            thread.join()
            if self.thread is thread:
                break

    def latest_report(self, student: str) -> dict | None:
        # Custom wrappers use only their own cached JSON, never guessed engine paths.
        if self.workflow.resolve() != (self.root / "khan-mastery-sync").resolve():
            try:
                report = json.loads(self._result_path(student).read_text())
                return (
                    report
                    if isinstance(report, dict) and report.get("student") == student
                    else None
                )
            except (OSError, ValueError):
                return None
        path = self.root / "private" / f"{student.casefold().replace(' ', '-')}-reading-plan.json"
        try:
            payload = json.loads(path.read_text())
            if isinstance(payload, dict) and payload.get("student") == student:
                return build_dashboard_report(payload)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def checkin_history(self, student: str) -> dict:
        return checkin_history(self.root, student)

    def _result_path(self, student: str) -> Path:
        namespace = hashlib.sha256(str(self.workflow.resolve()).encode()).hexdigest()[:20]
        slug = student.casefold().replace(" ", "-")
        return self.root / "private/dashboard-results" / namespace / f"{slug}.json"


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int,
        job: SyncJob,
        *,
        token: str | None = None,
        token_path: Path | None = None,
    ) -> None:
        token = token or load_dashboard_token(
            token_path or job.root / "private/dashboard-token.local"
        )
        if not DASHBOARD_TOKEN_PATTERN.fullmatch(token):
            raise AutomationError("Dashboard token is invalid")
        super().__init__(("127.0.0.1", port), DashboardHandler)
        self.job = job
        self.token = token
        self.origin = f"http://127.0.0.1:{self.server_port}"


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args: object) -> None:
        # Never log browser credentials or student records.
        pass

    def _reply(self, code: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
        )
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._reply(code, json.dumps(payload).encode())

    def _allowed(self, *, authenticate: bool) -> bool:
        expected_host = self.server.origin.removeprefix("http://")
        origin = self.headers.get("Origin")
        if self.headers.get("Host") != expected_host or (origin and origin != self.server.origin):
            self._json(403, {"error": "Local origin required"})
            return False
        token = self.headers.get("X-Tutor-Token", "")
        if authenticate and (
            not token.isascii() or not secrets.compare_digest(token, self.server.token)
        ):
            self._json(403, {"error": "Open the dashboard using its launch URL"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._allowed(authenticate=self.path.startswith("/api/")):
            return
        if self.path == "/api/setup":
            self._json(200, setup_status(self.server.job.root, self.server.job.serial))
        elif self.path == "/api/journey":
            students = setup_status(self.server.job.root, self.server.job.serial)["students"]
            self._json(200, self.server.job.journeys(students))
        elif urlsplit(self.path).path == "/api/status":
            snapshot = self.server.job.snapshot()
            phases = re.findall(r"Starting ((?:phase|teardown)\.\w+)", snapshot["output"])
            snapshot["phase"] = phases[-1] if phases else ""
            # Expose the existing public verification stage while retaining
            # separate checkbox/queue timings in the diagnostic trace.
            snapshot["phase"] = snapshot["phase"].replace(
                "verify_parent_checkbox", "verify_parent_assignment"
            )
            snapshot["progress_fraction"] = snapshot.get(
                "progress_fraction", sync_progress(snapshot["output"], snapshot["state"])
            )
            if parse_qs(urlsplit(self.path).query).get("diagnostics") != ["1"]:
                snapshot["output"] = ""
            self._json(200, snapshot)
        elif urlsplit(self.path).path == "/api/latest":
            student = parse_qs(urlsplit(self.path).query).get("student", [""])[0]
            if (
                student
                not in setup_status(self.server.job.root, self.server.job.serial)["students"]
            ):
                self._json(400, {"error": "Select a configured student"})
            else:
                self._json(200, {"report": self.server.job.latest_report(student)})
        elif urlsplit(self.path).path == "/api/history":
            student = parse_qs(urlsplit(self.path).query).get("student", [""])[0]
            if (
                student
                not in setup_status(self.server.job.root, self.server.job.serial)["students"]
            ):
                self._json(400, {"error": "Select a configured student"})
            else:
                self._json(200, self.server.job.checkin_history(student))
        else:
            assets = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            if self.path not in assets:
                self._json(404, {"error": "Not found"})
                return
            name, kind = assets[self.path]
            self._reply(200, (ASSETS / name).read_bytes(), kind)

    def do_POST(self) -> None:
        self.connection.settimeout(5)
        if not self._allowed(authenticate=True):
            return
        if self.headers.get("Origin") != self.server.origin:
            self._json(403, {"error": "Local origin required"})
            return
        if self.path not in {
            "/api/sync",
            "/api/assignment",
            "/api/connection-check",
            "/api/finish-cleanup",
            "/api/stop",
        }:
            self._json(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096 or self.headers.get("Content-Type") != "application/json":
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            fields = {
                "/api/sync": {"student"},
                "/api/assignment": {"student", "grade", "title", "variant", "action"},
                "/api/connection-check": set(),
                "/api/finish-cleanup": set(),
                "/api/stop": set(),
            }[self.path]
            if not isinstance(payload, dict) or set(payload) != fields:
                raise ValueError
            setup = setup_status(self.server.job.root, self.server.job.serial)
            if self.path in {"/api/sync", "/api/assignment"} and (
                not isinstance(payload["student"], str)
                or payload["student"] not in setup["students"]
            ):
                raise ValueError
            assignment = None
            if self.path == "/api/assignment":
                if (
                    self.server.job.workflow.resolve()
                    != (self.server.job.root / "khan-mastery-sync").resolve()
                ):
                    raise AutomationError("Lesson assignment requires the native workflow")
                assignment = ManualChange.from_dict(
                    {key: value for key, value in payload.items() if key != "student"},
                    CatalogIndex(self.server.job.root / "data/reading-ela-archive.json"),
                ).as_dict()
        except (ValueError, OSError, AutomationError, KeyError, TypeError):
            self._json(
                400,
                {
                    "error": "Select a configured student and an exact catalog lesson variant; only assign or unassign is accepted"
                },
            )
            return
        if self.path == "/api/connection-check":
            result = self.server.job.check_connection()
            self._json(200 if result.get("ready") else 409, result)
            return
        if self.path == "/api/finish-cleanup":
            result = self.server.job.finish_cleanup()
            self._json(200 if result.get("ready") else 409, result)
            return
        if self.path == "/api/stop":
            stopped = self.server.job.request_stop()
            self._json(
                202 if stopped else 409,
                {"state": "stopping"}
                if stopped
                else {"error": "No safely stoppable mastery sync is running"},
            )
            return
        if payload["student"] in setup.get("archived_students", []):
            self._json(
                409,
                {
                    "error": "This reader has archived history only. Switch accounts and configure a live profile before syncing."
                },
            )
        elif not setup["ready"]:
            self._json(409, {"error": "Complete the setup checklist first"})
        else:
            try:
                started = (
                    self.server.job.start(payload["student"], assignment=assignment)
                    if assignment
                    else self.server.job.start(payload["student"])
                )
            except (AutomationError, OSError, ValueError, KeyError, TypeError):
                self._json(
                    409,
                    {
                        "error": "The lesson catalog changed or could not be validated. Refresh before trying again."
                    },
                )
                return
            self._json(
                202 if started else 409,
                {"state": "running"} if started else {"error": "A sync is already running"},
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--open-existing",
        action="store_true",
        help="open the private URL for an already-running dashboard and exit",
    )
    parser.add_argument("--serial", help="Explicit ADB serial, e.g. a USB-connected tablet")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save owner-private assignment navigation traces and failure captures",
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=ROOT / "khan-mastery-sync",
        help="Trusted local sync wrapper, including an isolated family launcher",
    )
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.open_existing:
        token = load_dashboard_token(ROOT / "private/dashboard-token.local")
        url = f"http://127.0.0.1:{args.port}/#{token}"
        print("Opening the private local dashboard.", flush=True)
        webbrowser.open(url)
        return
    workflow = args.workflow.resolve()
    if not workflow.is_file():
        parser.error("workflow must be an existing local sync wrapper")
    job = SyncJob(ROOT, workflow, args.serial, debug=args.debug)

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    with DashboardServer(args.port, job) as server:
        url = server.origin + "/#" + server.token
        print(f"Local dashboard: {url}", flush=True)
        if args.debug:
            print(
                "Debug mode: private/dashboard-debug/events.jsonl (owner-private; no automatic request replay)",
                flush=True,
            )
        print(
            "Keep this launch URL private. Ctrl+C stops the dashboard after any active sync finishes.",
            flush=True,
        )
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Waiting for any active sync to finish safely...", flush=True)
        finally:
            job.wait()


if __name__ == "__main__":
    main()
