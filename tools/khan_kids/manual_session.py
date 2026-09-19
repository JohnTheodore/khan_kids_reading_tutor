"""Direct, verified parent edits; deliberately never runs the mastery planner."""

from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path

from .adb import AndroidDevice, AutomationError
from .automation import KhanKidsAutomation
from .catalog import CatalogIndex
from .constants import KHAN_KIDS_PACKAGE
from .device_discovery import DeviceConfig, resolve_device
from .diagnostics import capture_device_failure
from .launcher import ensure_khan_kids_open, local_secrets_provider
from .manual_assignments import ManualAssignments, ManualChange, policy_path
from .records import record_action, write_json_atomic, write_text_atomic
from .sync_report import append_sync_report, build_dashboard_report
from .timing import TimingRecorder
from .ui import text_set
from .workflow_lock import exclusive_workflow_lock


class ManualAssignmentSession:
    """Hold one tablet/lock/awake session across several explicit parent requests."""

    def __init__(
        self,
        root: Path,
        serial: str | None,
        progress: Callable[[str], None],
        *,
        debug: bool = False,
    ) -> None:
        self.root, self.serial, self.progress = root, serial, progress
        self.resources = ExitStack()
        self.automation: KhanKidsAutomation | None = None
        self.debug = debug
        self.trace_directory: Path | None = None
        self.trace_sequence = 0
        self.trace_state: str | None = None

    def __enter__(self) -> ManualAssignmentSession:
        try:
            self.resources.enter_context(
                exclusive_workflow_lock(self.root / "private/.reading-workflow.lock")
            )
            serial = self.serial or resolve_device(
                DeviceConfig.load(self.root / "private/tablet-device.local.json")
            )
            self.device = AndroidDevice(serial, timing=TimingRecorder(self.progress))
            self.device.assert_connected()
            self.resources.enter_context(self.device.app_session(KHAN_KIDS_PACKAGE))
            self.scratch = Path(
                self.resources.enter_context(tempfile.TemporaryDirectory(prefix="khan-parent-"))
            )
            self.device.enable_ui_backend("uiautomator2")
            self.credentials = local_secrets_provider(self.root / ".secrets.json")
            self.catalog = CatalogIndex(self.root / "data/reading-ela-archive.json")
            return self
        except BaseException:
            self.resources.close()
            raise

    def apply(self, student: str, raw_change: dict) -> dict:
        return self.apply_many(student, raw_change)[0]

    def apply_many(
        self,
        student: str,
        raw_change: dict,
        next_change: Callable[[], dict | None] = lambda: None,
    ) -> list[dict]:
        """Drain one ordered grade traversal, then verify its queue once."""
        self.current_journal = None
        self.batch_entries: list[dict] = []
        self.verified_reports: list[dict] = []
        self.trace_directory = None
        if self.debug:
            self._start_trace("assignment-")
        try:
            return self._apply_many(student, raw_change, next_change)
        except Exception as error:
            try:
                if self.trace_directory is None:
                    self._start_trace("failure-")
                self._capture_failure(error)
            except Exception:
                self.progress("Private failure capture unavailable; original failure preserved")
            if self.current_journal is not None:
                try:
                    payload = json.loads(self.current_journal.read_text())
                    # Read-only reconciliation: never replay a Save after a failure.
                    self._reconcile_batch()
                    payload["batch_operations"] = json.loads(self.current_journal.read_text()).get(
                        "batch_operations", []
                    )
                    payload.update(
                        status="interrupted",
                        error=str(error),
                        interrupted_at=datetime.now().astimezone().isoformat(),
                        recovery={
                            "status": "partial" if self.verified_reports else "unavailable",
                            "verified_changes": [r["manual_change"] for r in self.verified_reports],
                            "error": "Unverified edits require review; no automatic replay",
                        },
                    )
                    write_json_atomic(self.current_journal, payload)
                    slug = student.casefold().replace(" ", "-")
                    append_sync_report(
                        self.root / f"student-records/{slug}-reading-sync-log.md",
                        {
                            **payload,
                            "error": "Parent edit was not verified. Check the tablet before retrying.",
                        },
                    )
                except Exception:
                    self.progress(
                        "Parent operation journal could not be finalized; check the tablet before retrying."
                    )
            raise

    def _start_trace(self, prefix: str) -> None:
        parent = self.root / "private/dashboard-debug"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.trace_directory = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        self.trace_sequence = 0
        self.trace_state = None

    def _trace_hierarchy(self, root: ET.Element) -> None:
        if self.trace_directory is None or self.automation is None:
            return
        self.trace_sequence += 1
        state = self.automation._navigation_state(root)
        self.progress(f"Debug UI {self.trace_sequence}: {state or 'unrecognized'}")
        # Never retain password-dialog XML, even in owner-private debug storage.
        if self.trace_sequence <= 200 and "Enter Password" not in text_set(root):
            write_text_atomic(
                self.trace_directory / f"ui-{self.trace_sequence:04d}.xml",
                ET.tostring(root, encoding="unicode"),
            )
            if state != self.trace_state and state in {
                "profile_chooser",
                "teacher_roster",
                "assignments_report",
                "all_progress_report",
                "class_reports_menu",
                "report_tabs",
            }:
                destination = self.trace_directory / f"state-{self.trace_sequence:04d}-{state}.png"
                destination.touch(mode=0o600)
                self.device.screenshot(destination)
            self.trace_state = state

    def _capture_failure(self, error: Exception) -> None:
        assert self.trace_directory is not None
        capture_device_failure(self.trace_directory, self.device, error, progress=self.progress)
        self.progress(
            f"Private debug evidence saved: private/dashboard-debug/{self.trace_directory.name}"
        )

    def _apply_many(
        self, student: str, raw_change: dict, next_change: Callable[[], dict | None]
    ) -> list[dict]:
        change = ManualChange.from_dict(raw_change, self.catalog)
        if student not in self.catalog.roster:
            raise AutomationError("The configured student is not in the lesson catalog")
        timing = self.device.timing = TimingRecorder(self.progress)
        if self.automation is None or self.automation.student != student:
            self.automation = KhanKidsAutomation(
                self.device,
                student=student,
                roster=self.catalog.roster,
                scratch=self.scratch,
                parent_password_provider=lambda: self.credentials().khan_parent_password,
                hierarchy_observer=self._trace_hierarchy if self.debug else None,
            )
            with timing.span("startup.launch"):
                ensure_khan_kids_open(
                    self.device,
                    pin_provider=lambda: self.credentials().android_pin,
                    fresh_start=True,
                    reuse_ready=self.automation.ready_for_sync,
                )
        grade = change.activity.grade
        seen = set()
        while raw_change is not None:
            change = ManualChange.from_dict(raw_change, self.catalog)
            if change.activity.grade != grade or change.activity.key in seen:
                raise AutomationError("Parent batch must contain distinct variants in one grade")
            seen.add(change.activity.key)
            self._save_entry(student, change, reset_to_top=len(seen) == 1)
            raw_change = next_change()
        with timing.span("phase.verify_parent_assignment"):
            keys = self._live_queue_keys()
            for entry in self.batch_entries:
                self._verify_membership(entry, keys)
        for entry in self.batch_entries:
            self.verified_reports.append(self._finish_entry(entry, keys))
        return self.verified_reports

    def _save_entry(self, student: str, change: ManualChange, *, reset_to_top: bool) -> None:
        automation = self.automation
        slug = student.casefold().replace(" ", "-")
        journal = self.root / f"private/{slug}-manual-operation.json"
        self.current_journal = journal
        payload = {
            "student": student,
            "manual_change": change.as_dict(),
            "status": "applying",
            "generated_at": datetime.now().astimezone().isoformat(),
            "actions": [],
            "desired_assignments": [],
            "applied": [],
        }
        entry = {"change": change, "payload": payload, "locally_verified": False, "result": None}
        self.batch_entries.append(entry)
        self._write_batch_journal(payload)
        # Persist explicit intent under the same tablet lock, before writing the UI.
        preferences = policy_path(self.root, student)
        ManualAssignments.load(preferences, student, self.catalog).changed(change).save(preferences)
        desired = "checked" if change.action == "assign" else "unchecked"
        reason = (
            "parent assigned this variant; protected until mastered or unassigned"
            if change.action == "assign"
            else "parent unassigned this variant; automatic reassignment is paused"
        )
        payload["requested_action"] = {
            **change.activity.as_dict(),
            "kind": "add" if change.action == "assign" else "remove",
            "reason": reason,
        }
        self._write_batch_journal(payload)
        with self.device.timing.span("phase.save_parent_assignment"):
            result, before = automation.set_catalog_assignment(
                change.activity.grade,
                *change.activity.key,
                assigned=change.action == "assign",
                reset_to_top=reset_to_top,
            )
        entry["result"] = result
        changed = result is not None
        if changed:
            payload["actions"] = [payload["requested_action"]]
            payload["applied"] = [
                {"action": result.action, "title": result.title, "variant": result.variant}
            ]
        self._write_batch_journal(payload)
        with self.device.timing.span("phase.verify_parent_checkbox"):
            after = automation.inspect_catalog_assignment(
                change.activity.grade, *change.activity.key, reset_to_top=False
            )
            expected = {**before, student: desired}
            if after != expected:
                raise AutomationError("Saved assignment failed exact-student checkbox verification")
        entry["locally_verified"] = True
        self._write_batch_journal(payload)

    @staticmethod
    def _verify_membership(entry: dict, keys: set[tuple[str, str]]) -> None:
        change = entry["change"]
        if (change.activity.key in keys) != (change.action == "assign"):
            raise AutomationError("Saved parent assignment is not visible in the live queue")

    def _live_queue_keys(self) -> set[tuple[str, str]]:
        snapshot = self.automation.scan_assignments(
            today=date.today(), include_score_histories=False
        )
        return {(row.title, row.variant) for row in snapshot.rows}

    def _write_batch_journal(self, payload: dict) -> None:
        payload["batch_operations"] = [
            {
                "manual_change": entry["change"].as_dict(),
                "saved": bool(entry["payload"]["applied"]),
                "checkbox_verified": entry["locally_verified"],
                "status": entry["payload"]["status"],
            }
            for entry in self.batch_entries
        ]
        write_json_atomic(self.current_journal, payload)

    def _reconcile_batch(self) -> None:
        candidates = [
            entry
            for entry in self.batch_entries
            if entry["locally_verified"] and entry["payload"]["status"] == "applying"
        ]
        if not candidates:
            return
        try:
            keys = self._live_queue_keys()
            for entry in candidates:
                try:
                    self._verify_membership(entry, keys)
                except AutomationError:
                    continue
                self.verified_reports.append(self._finish_entry(entry, keys))
        except Exception:
            self.progress(
                "Read-only batch reconciliation unavailable; saved edits were not replayed"
            )

    def _finish_entry(self, entry: dict, keys: set[tuple[str, str]]) -> dict:
        payload, result = entry["payload"], entry["result"]
        student = payload["student"]
        slug = student.casefold().replace(" ", "-")
        saved_plan = self.root / f"private/{slug}-reading-plan.json"
        changed = result is not None
        # Carry forward previously saved evidence, without inventing fresh scores/dates.
        if saved_plan.exists():
            previous = json.loads(saved_plan.read_text())
            if previous.get("student") == student:
                payload["score_evidence"] = previous.get("score_evidence", [])
        activities = [{"title": title, "variant": variant} for title, variant in sorted(keys)]
        payload.update(
            status="applied" if changed else "no_op",
            observed_assignments=activities,
            desired_assignments=activities,
            verified_assignments=activities,
            performance=self.device.timing.snapshot(),
        )
        if changed:
            record_action(
                self.root / f"student-records/{slug}-assignment-actions.csv",
                action_date=date.today(),
                student=student,
                action=result.action,
                title=result.title,
                variant=result.variant,
                reason=payload["requested_action"]["reason"],
                result="saved; verified",
            )
        self._write_batch_journal(payload)
        write_json_atomic(saved_plan, payload)
        append_sync_report(self.root / f"student-records/{slug}-reading-sync-log.md", payload)
        return build_dashboard_report(payload)

    def __exit__(self, kind, error, traceback) -> None:
        cleanup_error = error
        try:
            # Failure leaves unexpected screens intact for diagnosis.
            if error is None and self.automation is not None:
                with self.device.timing.span("teardown.switch_user"):
                    self.automation.return_to_profile_chooser()
        except BaseException as failure:
            cleanup_error = failure
            raise
        finally:
            self.resources.__exit__(
                type(cleanup_error) if cleanup_error is not None else kind,
                cleanup_error,
                cleanup_error.__traceback__ if cleanup_error is not None else traceback,
            )
