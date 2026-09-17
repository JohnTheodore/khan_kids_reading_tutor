"""Direct, verified parent edits; deliberately never runs the mastery planner."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path

from .adb import AndroidDevice, AutomationError
from .automation import KhanKidsAutomation
from .catalog import CatalogIndex
from .device_discovery import DeviceConfig, resolve_device
from .launcher import ensure_khan_kids_open, local_secrets_provider
from .manual_assignments import ManualAssignments, ManualChange, policy_path
from .records import record_action, write_json_atomic
from .sync_report import append_sync_report, build_dashboard_report
from .timing import TimingRecorder
from .workflow_lock import exclusive_workflow_lock


class ManualAssignmentSession:
    """Hold one tablet/lock/awake session across several explicit parent requests."""

    def __init__(self, root: Path, serial: str | None, progress: Callable[[str], None]) -> None:
        self.root, self.serial, self.progress = root, serial, progress
        self.resources = ExitStack()
        self.automation: KhanKidsAutomation | None = None

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
            self.resources.enter_context(self.device.awake_session())
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
        self.current_journal = None
        try:
            return self._apply(student, raw_change)
        except Exception as error:
            if self.current_journal is not None:
                try:
                    payload = json.loads(self.current_journal.read_text())
                    payload.update(
                        status="interrupted",
                        error=str(error),
                        interrupted_at=datetime.now().astimezone().isoformat(),
                        recovery={
                            "status": "unavailable",
                            "error": "Exact parent edit was not verified",
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

    def _apply(self, student: str, raw_change: dict) -> dict:
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
            )
            with timing.span("startup.launch"):
                ensure_khan_kids_open(
                    self.device,
                    pin_provider=lambda: self.credentials().android_pin,
                    fresh_start=True,
                    reuse_ready=self.automation.ready_for_sync,
                )
        automation = self.automation
        slug = student.casefold().replace(" ", "-")
        journal = self.root / f"private/{slug}-manual-operation.json"
        self.current_journal = journal
        saved_plan = self.root / f"private/{slug}-reading-plan.json"
        payload = {
            "student": student,
            "manual_change": change.as_dict(),
            "status": "applying",
            "generated_at": datetime.now().astimezone().isoformat(),
            "actions": [],
            "desired_assignments": [],
            "applied": [],
        }
        write_json_atomic(journal, payload)
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
        write_json_atomic(journal, payload)
        with timing.span("phase.save_parent_assignment"):
            result, before = automation.set_catalog_assignment(
                change.activity.grade, *change.activity.key, assigned=change.action == "assign"
            )
        changed = result is not None
        if changed:
            payload["actions"] = [payload["requested_action"]]
            payload["applied"] = [
                {"action": result.action, "title": result.title, "variant": result.variant}
            ]
            write_json_atomic(journal, payload)
        with timing.span("phase.verify_parent_assignment"):
            after = automation.inspect_catalog_assignment(
                change.activity.grade, *change.activity.key
            )
            expected = {**before, student: desired}
            if after != expected:
                raise AutomationError("Saved assignment failed exact-student checkbox verification")
            snapshot = automation.scan_assignments(
                today=date.today(), include_score_histories=False
            )
            keys = {(row.title, row.variant) for row in snapshot.rows}
            if (change.activity.key in keys) != (change.action == "assign"):
                raise AutomationError("Saved parent assignment is not visible in the live queue")
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
            performance=timing.snapshot(),
        )
        if changed:
            record_action(
                self.root / f"student-records/{slug}-assignment-actions.csv",
                action_date=date.today(),
                student=student,
                action=result.action,
                title=result.title,
                variant=result.variant,
                reason=reason,
                result="saved; verified",
            )
        write_json_atomic(journal, payload)
        write_json_atomic(saved_plan, payload)
        append_sync_report(self.root / f"student-records/{slug}-reading-sync-log.md", payload)
        return build_dashboard_report(payload)

    def __exit__(self, kind, error, traceback) -> None:
        try:
            # Failure leaves unexpected screens intact for diagnosis.
            if error is None and self.automation is not None:
                with self.device.timing.span("teardown.switch_user"):
                    self.automation.return_to_profile_chooser()
        finally:
            self.resources.close()
