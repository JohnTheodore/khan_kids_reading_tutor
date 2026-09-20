#!/usr/bin/env python3
"""Reconcile one student's Khan Kids reading queue from a reviewed plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path

from khan_kids.adb import AndroidDevice, AutomationError, HomeHandoffError
from khan_kids.automation import ActionResult, KhanKidsAutomation
from khan_kids.cancellation import FileCancellationToken
from khan_kids.catalog import CatalogIndex
from khan_kids.checkin_history import record_checkin
from khan_kids.constants import KHAN_KIDS_PACKAGE
from khan_kids.curriculum import Activity, ReadingCurriculum
from khan_kids.diagnostics import DiagnosticRun, new_run_id
from khan_kids.history_cache import HistoryCache
from khan_kids.incidents import append_failed_sync_incident
from khan_kids.launcher import ensure_khan_kids_open, local_secrets_provider
from khan_kids.manual_assignments import (
    ManualAssignments,
    ManualChange,
    plan_parent_queue,
    policy_path,
)
from khan_kids.mastery import evaluate_mastery
from khan_kids.planner import (
    QueueAction,
    QueuePlan,
    TrackState,
    build_queue_plan,
    snapshot_fingerprint,
)
from khan_kids.preflight import assert_tablet_preflight
from khan_kids.progress import ProgressReporter
from khan_kids.quarantine import (
    LessonQuarantine,
    append_low_score_quarantines,
    read_active_quarantines,
)
from khan_kids.records import (
    ATTEMPT_FIELDS,
    ATTEMPT_ID_FIELDS,
    append_unique_rows_with_records,
    read_attempt_scores,
    read_mastered_action_keys,
    record_action,
    write_json_atomic,
)
from khan_kids.reports import AssignmentSnapshot
from khan_kids.student_identity import public_student
from khan_kids.sync_report import (
    append_performance_report,
    append_sync_report,
    build_dashboard_report,
    recommend_next_lessons,
    render_terminal_summary,
    terminal_color_enabled,
)
from khan_kids.timing import TimingRecorder
from khan_kids.workflow import histories_to_attempt_rows, overlay_live_scores
from khan_kids.workflow_lock import exclusive_workflow_lock

PLAN_VERSION = 4
INCIDENT_LOG_PATH = Path("INCIDENTS.md")
WORKFLOW_LOCK_PATH = Path("private/.reading-workflow.lock")


class MasterySyncInterrupted(AutomationError):
    """Carry the structured, already-persisted failure result to the CLI."""

    def __init__(self, cause: Exception, payload: dict[str, object]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.payload = payload


def create_plan_payload(
    *,
    student: str,
    snapshot: AssignmentSnapshot,
    plan: QueuePlan,
    curriculum: ReadingCurriculum,
    catalog_path: Path,
    curriculum_path: Path,
    new_attempt_records: int,
    generated_at: datetime,
    scores: dict[tuple[str, str], tuple[int, ...]] | None = None,
    new_attempts: list[dict[str, str]] | None = None,
    active_quarantines: tuple[LessonQuarantine, ...] = (),
    mastered_keys: set[tuple[str, str]] | None = None,
    score_scan_mode: str = "cache_eligible",
    manual_policy: ManualAssignments | None = None,
    manual_change: ManualChange | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    stretch_keys = {
        activity.key for stretch in curriculum.stretch_pool for activity in stretch.activities
    }
    score_map = scores or {}
    mastered_keys = mastered_keys or set()
    relevant_keys = dict.fromkeys(
        [action.key for action in plan.actions] + [activity.key for activity in plan.desired]
    )
    return {
        "version": PLAN_VERSION,
        "status": "review_required",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "student": student,
        "path_id": curriculum.path_id,
        "path_name": curriculum.name,
        "path_objective": curriculum.objective,
        **({"run_id": run_id} if run_id else {}),
        "catalog_sha256": _file_digest(catalog_path),
        "curriculum_sha256": _file_digest(curriculum_path),
        "observed_state_sha256": snapshot_fingerprint(snapshot),
        "new_attempt_records": new_attempt_records,
        "new_attempts": [
            {
                "attempt_date": row.get("attempt_date", ""),
                "title": row.get("lesson_title", ""),
                "variant": row.get("activity_variant", ""),
                "score": int(row.get("score_percent", 0)),
            }
            for row in (new_attempts or [])
        ],
        "score_scan_mode": score_scan_mode,
        "score_controls_read": [
            {
                "title": history.title,
                "variant": history.variant,
                "attempts_newest_first": [
                    {
                        "attempt_date": attempt.attempt_date.isoformat(),
                        "score": attempt.score,
                    }
                    for attempt in history.attempts_newest_first
                ],
            }
            for history in snapshot.histories
        ],
        "active_quarantines": [record.as_dict() for record in active_quarantines],
        "quarantine_state_sha256": _quarantine_digest(active_quarantines),
        "mastery_state_sha256": _mastery_digest(mastered_keys),
        **({"manual_state_sha256": manual_policy.digest} if manual_policy else {}),
        **({"manual_change": manual_change.as_dict()} if manual_change else {}),
        "observed_assignments": [
            {
                "title": row.title,
                "variant": row.variant,
                "assigned_date": row.assigned_date,
                "score": row.score,
            }
            for row in snapshot.rows
        ],
        "desired_assignments": [activity.as_dict() for activity in plan.desired],
        "stretch_assignments": [
            activity.as_dict() for activity in plan.desired if activity.key in stretch_keys
        ],
        "actions": [action.as_dict() for action in plan.actions],
        "track_states": [_track_payload(state) for state in plan.tracks],
        "score_evidence": [
            _score_evidence_payload(
                title,
                variant,
                score_map.get((title, variant), ()),
                mastered=(title, variant) in mastered_keys,
            )
            for title, variant in relevant_keys
        ],
    }


def validate_reviewed_plan(
    payload: dict[str, object],
    *,
    student: str,
    snapshot: AssignmentSnapshot,
    catalog_path: Path,
    curriculum_path: Path,
    curriculum: ReadingCurriculum,
    active_quarantines: tuple[LessonQuarantine, ...] = (),
    mastered_keys: set[tuple[str, str]] | None = None,
    manual_policy: ManualAssignments | None = None,
    scores: dict[tuple[str, str], tuple[int, ...]] | None = None,
) -> tuple[tuple[Activity, ...], tuple[QueueAction, ...]]:
    _validate_plan_metadata(
        payload,
        student=student,
        catalog_path=catalog_path,
        curriculum_path=curriculum_path,
    )
    expected_state = snapshot_fingerprint(snapshot)
    if payload.get("observed_state_sha256") != expected_state:
        raise AutomationError("Reviewed plan is stale; changed inputs: observed_state_sha256")
    if payload.get("quarantine_state_sha256") != _quarantine_digest(active_quarantines):
        raise AutomationError("Reviewed plan is stale; changed inputs: quarantine_state_sha256")
    if payload.get("mastery_state_sha256") != _mastery_digest(mastered_keys or set()):
        raise AutomationError("Reviewed plan is stale; changed inputs: mastery_state_sha256")

    raw_desired = payload.get("desired_assignments")
    raw_actions = payload.get("actions")
    if not isinstance(raw_desired, list) or not all(isinstance(item, dict) for item in raw_desired):
        raise AutomationError("Reviewed plan has invalid desired assignments")
    if not isinstance(raw_actions, list) or not all(isinstance(item, dict) for item in raw_actions):
        raise AutomationError("Reviewed plan has invalid actions")
    desired = tuple(Activity.from_dict(item) for item in raw_desired)
    actions = tuple(QueueAction.from_dict(item) for item in raw_actions)
    permitted = curriculum.activities_by_key
    if manual_policy is not None:
        if payload.get("manual_state_sha256") != manual_policy.digest:
            raise AutomationError("Reviewed plan is stale; changed inputs: manual_state_sha256")
        catalog = CatalogIndex(catalog_path)
        raw_change = payload.get("manual_change")
        if raw_change is not None and not isinstance(raw_change, dict):
            raise AutomationError("Reviewed plan has invalid parent assignment metadata")
        change = (
            ManualChange.from_dict(raw_change, catalog) if isinstance(raw_change, dict) else None
        )
        expected = plan_parent_queue(
            curriculum,
            catalog,
            scores or {},
            _assignment_keys(snapshot),
            manual_policy,
            quarantined_titles={record.title: record.reason for record in active_quarantines},
            mastered_keys=mastered_keys,
            change=change,
        )
        if {a.key: a for a in desired} != {a.key: a for a in expected.desired}:
            raise AutomationError(
                "Reviewed queue no longer matches the current parent preferences and scores"
            )
        parent_keys = {a.key for a in manual_policy.assigned + manual_policy.excluded}
        if tuple(a for a in actions if a.key in parent_keys) != tuple(
            a for a in expected.actions if a.key in parent_keys
        ):
            raise AutomationError(
                "Reviewed parent actions no longer match the saved preferences and mastery evidence"
            )
        if not change and len(desired) < curriculum.queue_limit:
            raise AutomationError("Reviewed automatic queue cannot fall below its target")
        permitted = {a.key: a for a in expected.desired}
    elif len(desired) != curriculum.queue_limit:
        raise AutomationError(
            f"Reviewed plan must contain exactly {curriculum.queue_limit} assignments; "
            f"found {len(desired)}"
        )
    for activity in desired:
        if permitted.get(activity.key) != activity:
            raise AutomationError(f"Reviewed plan contains an unapproved activity: {activity}")
    _validate_action_result(snapshot, desired, actions)
    return desired, actions


def _validate_plan_metadata(
    payload: dict[str, object],
    *,
    student: str,
    catalog_path: Path,
    curriculum_path: Path,
) -> None:
    if payload.get("version") != PLAN_VERSION:
        raise AutomationError("Reviewed plan has an unsupported version")
    if payload.get("status") != "review_required":
        raise AutomationError("Reviewed plan is not pending application")
    if payload.get("student") != student:
        raise AutomationError("Reviewed plan belongs to a different student")
    expected_digests = {
        "catalog_sha256": _file_digest(catalog_path),
        "curriculum_sha256": _file_digest(curriculum_path),
    }
    changed = [key for key, value in expected_digests.items() if payload.get(key) != value]
    if changed:
        raise AutomationError(f"Reviewed plan is stale; changed inputs: {', '.join(changed)}")


def _validate_action_result(
    snapshot: AssignmentSnapshot,
    desired: tuple[Activity, ...],
    actions: tuple[QueueAction, ...],
) -> None:
    current = {(row.title, row.variant) for row in snapshot.rows}
    desired_by_key = {activity.key: activity for activity in desired}
    if len(desired_by_key) != len(desired):
        raise AutomationError("Reviewed plan contains duplicate desired assignments")
    desired_keys = set(desired_by_key)
    removals = [action for action in actions if action.kind == "remove"]
    additions = [action for action in actions if action.kind == "add"]
    removal_keys = [action.key for action in removals]
    addition_keys = [action.key for action in additions]
    if len(removal_keys) != len(set(removal_keys)) or len(addition_keys) != len(set(addition_keys)):
        raise AutomationError("Reviewed plan contains duplicate actions")
    if set(removal_keys) != current - desired_keys:
        raise AutomationError("Reviewed removals do not exactly match the desired queue")
    if set(addition_keys) != desired_keys - current:
        raise AutomationError("Reviewed additions do not exactly match the desired queue")
    for action in additions:
        target = desired_by_key[action.key]
        if action.grade != target.grade:
            raise AutomationError(f"Plan addition has an invalid grade: {action.key!r}")


def main(progress: ProgressReporter | None = None, *, run_id: str | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="ADB serial, usually IP:port")
    parser.add_argument("--student", required=True)
    parser.add_argument("--run-id", default=run_id or new_run_id(), help=argparse.SUPPRESS)
    parser.add_argument(
        "--secrets-file",
        type=Path,
        help="owner-private JSON file (default: .secrets.json when present)",
    )
    parser.add_argument("--catalog", type=Path, default=Path("data/reading-ela-archive.json"))
    parser.add_argument("--curriculum", type=Path, default=Path("data/reading-curriculum.json"))
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--actions", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--apply-plan", type=Path)
    parser.add_argument(
        "--sync",
        action="store_true",
        help="review and immediately apply a non-empty plan in one device session",
    )
    parser.add_argument(
        "--ui-backend",
        choices=("uiautomator2", "legacy-adb"),
        default="uiautomator2",
    )
    parser.add_argument("--history-cache", type=Path)
    parser.add_argument("--quarantines", type=Path)
    parser.add_argument(
        "--full-score-scan",
        action="store_true",
        help="ignore the same-day score-history cache",
    )
    parser.add_argument("--max-actions", type=int, default=20)
    parser.add_argument("--assignment-action", choices=("assign", "unassign"))
    parser.add_argument("--assignment-grade")
    parser.add_argument("--assignment-title")
    parser.add_argument("--assignment-variant")
    parser.add_argument("--cancel-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit compact machine-readable output instead of the default readable summary",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize readable output (default: auto; NO_COLOR disables auto color)",
    )
    args = parser.parse_args()
    args.student = public_student(args.student)
    diagnostics = DiagnosticRun(Path.cwd(), args.run_id, student=args.student)
    if sum(bool(value) for value in (args.plan, args.apply_plan, args.sync)) > 1:
        parser.error("--plan, --apply-plan, and --sync are mutually exclusive")
    if args.max_actions < 1:
        parser.error("--max-actions must be positive")
    assignment_fields = (
        args.assignment_action,
        args.assignment_grade,
        args.assignment_title,
        args.assignment_variant,
    )
    if any(assignment_fields) and (not all(assignment_fields) or not args.sync):
        parser.error("Parent assignments require --sync and all four --assignment-* fields")

    slug = args.student.casefold().replace(" ", "-")
    attempts_path = args.attempts or Path(f"student-records/{slug}-lesson-attempts.csv")
    actions_path = args.actions or Path(f"student-records/{slug}-assignment-actions.csv")
    report_path = args.report or Path(f"student-records/{slug}-reading-sync-log.md")
    plan_path = args.plan or Path(f"private/{slug}-reading-plan.json")
    cache_path = args.history_cache or Path(f"private/{slug}-score-history-cache.json")
    quarantine_path = args.quarantines or Path(f"student-records/{slug}-lesson-quarantines.csv")
    catalog = CatalogIndex(args.catalog)
    if args.student not in catalog.roster:
        parser.error(f"{args.student!r} is not in catalog roster {catalog.roster!r}")
    curriculum = ReadingCurriculum.load(args.curriculum, catalog)
    args.manual_policy_path = policy_path(Path.cwd(), args.student)
    manual_policy = ManualAssignments.load(args.manual_policy_path, args.student, catalog)
    args.manual_change = None
    if args.assignment_action:
        args.manual_change = ManualChange.from_dict(
            {
                "action": args.assignment_action,
                "grade": args.assignment_grade,
                "title": args.assignment_title,
                "variant": args.assignment_variant,
            },
            catalog,
        )
        manual_policy = manual_policy.changed(args.manual_change)
        # Save intent before tablet writes: interrupted saves can be reconciled.
        manual_policy.save(args.manual_policy_path)
    active_quarantines = read_active_quarantines(
        quarantine_path, student=args.student, today=args.today
    )
    mastered_keys = read_mastered_action_keys(actions_path, args.student)
    reviewed_payload = None
    if args.apply_plan:
        reviewed_payload = _read_object(args.apply_plan)
        _validate_plan_metadata(
            reviewed_payload,
            student=args.student,
            catalog_path=args.catalog,
            curriculum_path=args.curriculum,
        )

    timing = TimingRecorder(progress.emit if progress else None)
    cancellation = FileCancellationToken(args.cancel_file)
    device = AndroidDevice(args.serial, timing=timing, cancellation_check=cancellation.check)
    history_cache = HistoryCache.load(cache_path, student=args.student, today=args.today)
    output_payload: dict[str, object]
    teardown_error: AutomationError | None = None
    output_plan_path = args.apply_plan or plan_path
    try:
        with timing.span("startup.preflight"):
            assert_tablet_preflight(device)
        with (
            device.app_session(
                KHAN_KIDS_PACKAGE,
                on_error=lambda error: diagnostics.capture_device_failure(
                    device, error, progress=progress.emit if progress else None
                ),
            ),
            tempfile.TemporaryDirectory(prefix="khan-reading-") as temporary,
        ):
            credentials = local_secrets_provider(args.secrets_file)
            device.enable_ui_backend(args.ui_backend)
            automation = KhanKidsAutomation(
                device,
                student=args.student,
                roster=catalog.roster,
                scratch=Path(temporary),
                parent_password_provider=lambda: credentials().khan_parent_password,
                history_lookup=_history_lookup_for_run(args, history_cache),
                failure_capture=lambda error: diagnostics.capture_device_failure(
                    device, error, progress=progress.emit if progress else None
                ),
            )
            with timing.span("startup.launch"):
                ensure_khan_kids_open(
                    device,
                    pin_provider=lambda: credentials().android_pin,
                    fresh_start=True,
                    reuse_ready=automation.ready_for_sync,
                )
            with timing.span("phase.review_assignments"):
                snapshot = automation.scan_assignments(
                    today=args.today, include_score_histories=True
                )
            history_cache.update(snapshot.rows, snapshot.histories)
            history_cache.save()
            if args.apply_plan:
                assert reviewed_payload is not None
                reviewed_payload.setdefault("run_id", args.run_id)
                output_payload = _apply_reviewed_plan(
                    args=args,
                    payload=reviewed_payload,
                    snapshot=snapshot,
                    automation=automation,
                    actions_path=actions_path,
                    report_path=report_path,
                    curriculum=curriculum,
                    catalog=catalog,
                    active_quarantines=active_quarantines,
                    mastered_keys=mastered_keys,
                    plan_path=args.apply_plan,
                )
            else:
                output_payload = _review_snapshot(
                    args=args,
                    snapshot=snapshot,
                    automation=automation,
                    catalog=catalog,
                    curriculum=curriculum,
                    attempts_path=attempts_path,
                    actions_path=actions_path,
                    report_path=report_path,
                    plan_path=plan_path,
                    active_quarantines=active_quarantines,
                    mastered_keys=mastered_keys,
                )
            try:
                with timing.span("teardown.switch_user"):
                    automation.return_to_profile_chooser()
            except Exception as error:
                diagnostics.capture_device_failure(
                    device, error, progress=progress.emit if progress else None
                )
                output_payload["teardown"] = {
                    "status": "failed",
                    "error": str(error),
                }
                teardown_error = AutomationError(
                    "Teardown failed after the sync outcome "
                    f"{output_payload['status']!r} had already been saved: {error}"
                )
    except HomeHandoffError as error:
        diagnostics.capture_device_failure(
            device, error, progress=progress.emit if progress else None
        )
        output_payload["teardown"] = {
            "status": "failed",
            "kind": error.reason,
            "result_saved": True,
            "error": str(error),
        }
        teardown_error = AutomationError(
            f"Sync outcome was saved, but Android Home could not be verified: {error}"
        )
    except MasterySyncInterrupted as error:
        diagnostics.capture_device_failure(
            device, error.cause, progress=progress.emit if progress else None
        )
        raise
    except Exception as error:
        diagnostics.capture_device_failure(
            device, error, progress=progress.emit if progress else None
        )
        interrupted = _pre_apply_interruption_payload(
            args=args,
            curriculum=curriculum,
            device=device,
            history_cache=history_cache,
            timing=timing,
            error=error,
        )
        write_json_atomic(output_plan_path, interrupted)
        append_sync_report(report_path, interrupted)
        raise MasterySyncInterrupted(error, interrupted) from error

    timing_snapshot = timing.snapshot()
    output_payload.setdefault("run_id", args.run_id)
    output_payload["next_lesson_recommendations"] = recommend_next_lessons(output_payload)
    output_payload["performance"] = {
        "backend": device.ui_backend_name,
        "cache_hits": history_cache.hits,
        "cache_misses": history_cache.misses,
        **timing_snapshot,
        **(progress.snapshot() if progress else {}),
    }
    write_json_atomic(output_plan_path, output_payload)
    record_checkin(Path.cwd(), output_payload)
    append_performance_report(
        report_path,
        timing_snapshot,
        status=str(output_payload["status"]),
        backend=device.ui_backend_name,
        cache_hits=history_cache.hits,
        cache_misses=history_cache.misses,
    )
    summary = _summary(output_payload, plan_path=output_plan_path, report_path=report_path)
    summary["performance"] = output_payload["performance"]
    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        print(
            render_terminal_summary(
                output_payload,
                color=terminal_color_enabled(args.color, sys.stdout),
            )
        )
    if teardown_error is not None:
        raise MasterySyncInterrupted(teardown_error, output_payload)
    diagnostics.record_success(output_payload)


def _review_snapshot(
    *,
    args: argparse.Namespace,
    snapshot: AssignmentSnapshot,
    automation: KhanKidsAutomation,
    catalog: CatalogIndex,
    curriculum: ReadingCurriculum,
    attempts_path: Path,
    actions_path: Path,
    report_path: Path,
    plan_path: Path,
    active_quarantines: tuple[LessonQuarantine, ...],
    mastered_keys: set[tuple[str, str]],
) -> dict[str, object]:
    with automation.device.timing.span("phase.plan_queue"):
        manual_policy = None
        manual_change = getattr(args, "manual_change", None)
        if getattr(args, "manual_policy_path", None):
            manual_policy = ManualAssignments.load(args.manual_policy_path, args.student, catalog)
        preferred_grades = {
            key: activity.grade for key, activity in curriculum.activities_by_key.items()
        }
        if manual_policy:
            preferred_grades.update(
                {a.key: a.grade for a in manual_policy.assigned + manual_policy.excluded}
            )
        attempt_rows = histories_to_attempt_rows(
            snapshot.histories, catalog, preferred_grades=preferred_grades
        )
        appended_rows = append_unique_rows_with_records(
            attempts_path,
            ATTEMPT_FIELDS,
            attempt_rows,
            identity_fields=ATTEMPT_ID_FIELDS,
            reconcile_occurrences=True,
        )
        scores = overlay_live_scores(
            read_attempt_scores(attempts_path, args.student), snapshot.histories
        )
        active_quarantines = append_low_score_quarantines(
            args.quarantines
            or Path(
                f"student-records/{args.student.casefold().replace(' ', '-')}-lesson-quarantines.csv"
            ),
            student=args.student,
            today=args.today,
            scores=scores,
            new_attempts=appended_rows,
            active_quarantines=active_quarantines,
        )
        current = {(row.title, row.variant) for row in snapshot.rows}
        quarantine_reasons = {
            record.title: (
                f"through {record.active_through.isoformat()} (eligible again "
                f"{record.eligible_date.isoformat()}): {record.reason}"
            )
            for record in active_quarantines
        }
        if manual_policy:
            queue_plan = plan_parent_queue(
                curriculum,
                catalog,
                scores,
                current,
                manual_policy,
                quarantined_titles=quarantine_reasons,
                mastered_keys=mastered_keys,
                change=manual_change,
            )
        else:
            queue_plan = build_queue_plan(
                curriculum,
                scores,
                current,
                quarantined_titles=quarantine_reasons,
                mastered_keys=mastered_keys,
            )
        payload = create_plan_payload(
            student=args.student,
            snapshot=snapshot,
            plan=queue_plan,
            curriculum=curriculum,
            catalog_path=args.catalog,
            curriculum_path=args.curriculum,
            new_attempt_records=len(appended_rows),
            generated_at=datetime.now().astimezone(),
            scores=scores,
            new_attempts=appended_rows,
            active_quarantines=active_quarantines,
            mastered_keys=mastered_keys,
            manual_policy=manual_policy,
            manual_change=manual_change,
            run_id=getattr(args, "run_id", None),
            score_scan_mode=(
                "live_all_available" if args.sync or args.full_score_scan else "cache_eligible"
            ),
        )
        write_json_atomic(plan_path, payload)
    if not manual_change and len(queue_plan.desired) < curriculum.queue_limit:
        payload["queue_gap"] = curriculum.queue_limit - len(queue_plan.desired)
        payload["queue_block_reason"] = (
            f"Only {len(queue_plan.desired)} eligible assignments were found for the "
            f"required {curriculum.queue_limit}; no assignment changes were applied"
        )
        write_json_atomic(plan_path, payload)
        append_sync_report(report_path, payload)
        return payload
    if not queue_plan.actions:
        payload["status"] = "no_op"
        payload["verified_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_json_atomic(plan_path, payload)
        append_sync_report(report_path, payload)
        return payload
    if args.sync:
        return _apply_reviewed_plan(
            args=args,
            payload=payload,
            snapshot=snapshot,
            automation=automation,
            actions_path=actions_path,
            report_path=report_path,
            curriculum=curriculum,
            catalog=catalog,
            active_quarantines=active_quarantines,
            mastered_keys=mastered_keys,
            plan_path=plan_path,
        )
    append_sync_report(report_path, payload)
    return payload


def _apply_reviewed_plan(
    *,
    args: argparse.Namespace,
    payload: dict[str, object],
    snapshot: AssignmentSnapshot,
    automation: KhanKidsAutomation,
    actions_path: Path,
    report_path: Path,
    curriculum: ReadingCurriculum,
    catalog: CatalogIndex,
    active_quarantines: tuple[LessonQuarantine, ...] = (),
    mastered_keys: set[tuple[str, str]] | None = None,
    plan_path: Path | None = None,
) -> dict[str, object]:
    plan_path = plan_path or args.apply_plan
    if plan_path is None:
        raise AutomationError("A plan path is required for application")
    manual_policy = None
    scores = None
    if getattr(args, "manual_policy_path", None):
        manual_policy = ManualAssignments.load(args.manual_policy_path, args.student, catalog)
        attempts_path = args.attempts or Path(
            f"student-records/{args.student.casefold().replace(' ', '-')}-lesson-attempts.csv"
        )
        scores = overlay_live_scores(
            read_attempt_scores(attempts_path, args.student), snapshot.histories
        )
    desired, actions = validate_reviewed_plan(
        payload,
        student=args.student,
        snapshot=snapshot,
        catalog_path=args.catalog,
        curriculum_path=args.curriculum,
        curriculum=curriculum,
        active_quarantines=active_quarantines,
        mastered_keys=mastered_keys,
        manual_policy=manual_policy,
        scores=scores,
    )
    if len(actions) > args.max_actions:
        raise AutomationError(
            f"Reviewed plan contains {len(actions)} actions; limit is {args.max_actions}"
        )
    applied: list[dict[str, str]] = []
    desired_keys = {activity.key for activity in desired}
    current_keys = _assignment_keys(snapshot)
    action_by_key = {(action.kind, action.key): action for action in actions}
    payload["status"] = "applying"
    payload["operation_journal"] = {
        "status": "applying",
        "desired_state_sha256": _key_set_digest(desired_keys),
        "operations": [{**action.as_dict(), "state": "planned"} for action in actions],
    }
    write_json_atomic(plan_path, payload)
    try:
        while current_keys != desired_keys:
            missing = desired_keys - current_keys
            unexpected = current_keys - desired_keys
            if missing:
                action = min(
                    (action_by_key[("add", key)] for key in missing),
                    key=lambda candidate: catalog.order_key(candidate.grade, candidate.title),
                )
            elif unexpected:
                action = next(
                    action
                    for action in actions
                    if action.kind == "remove" and action.key in unexpected
                )
            else:
                raise AutomationError("Desired assignments could not be reconciled")

            with automation.device.timing.span(f"phase.{action.kind}_and_verify"):
                result = _apply_queue_action(automation, action)
                _record_applied_action(
                    result=result,
                    action=action,
                    actions_path=actions_path,
                    student=args.student,
                    action_date=args.today,
                    applied=applied,
                )
                _update_operation_journal(payload, action, state="saved")
                write_json_atomic(plan_path, payload)
                verified_snapshot = automation.scan_assignments(
                    today=args.today, include_score_histories=False
                )
                current_keys = _assignment_keys(verified_snapshot)
                expected_present = action.kind == "add"
                if (action.key in current_keys) is not expected_present:
                    raise AutomationError(
                        f"Saved {action.kind} was not visible in immediate queue verification: "
                        f"{action.title!r}/{action.variant!r}"
                    )
                _update_operation_journal(
                    payload,
                    action,
                    state="verified",
                    verified_queue=current_keys,
                )
                automation.device.timing.progress(
                    f"Verified {action.kind}: {action.title} — {action.variant}; "
                    f"live queue {len(current_keys)}"
                )
                write_json_atomic(plan_path, payload)

        with automation.device.timing.span("phase.fixed_point_verify"):
            fixed_point_snapshot = automation.scan_assignments(
                today=args.today, include_score_histories=False
            )
        fixed_point_keys = _assignment_keys(fixed_point_snapshot)
        if fixed_point_keys != desired_keys:
            raise AutomationError("Fixed-point verification did not match the desired queue")
    except Exception as error:
        recovery: dict[str, object]
        if automation.failure_capture is not None:
            with suppress(Exception):
                automation.failure_capture(error)
        try:
            # A stop request blocks new normal work, but must not block the one
            # read-only reconciliation needed to describe the live queue safely.
            with (
                automation.device.cleanup_mode(),
                automation.device.timing.span("phase.interruption_reconcile"),
            ):
                recovery_snapshot = automation.scan_assignments(
                    today=args.today, include_score_histories=False
                )
            live_keys = _assignment_keys(recovery_snapshot)
            recovery = _queue_recovery_payload(live_keys, desired_keys)
        except Exception as recovery_error:
            recovery = {"status": "unavailable", "error": str(recovery_error)}
        payload["status"] = "interrupted"
        payload["interrupted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        payload["error"] = str(error)
        payload["applied"] = applied
        payload["recovery"] = recovery
        _set_journal_status(payload, "interrupted")
        payload["performance"] = automation.device.timing.snapshot()
        write_json_atomic(plan_path, payload)
        append_sync_report(report_path, payload)
        raise MasterySyncInterrupted(error, payload) from error
    payload["status"] = "applied"
    payload["applied_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["applied"] = applied
    payload["verified_assignments"] = [
        {"title": title, "variant": variant} for title, variant in sorted(fixed_point_keys)
    ]
    _set_journal_status(payload, "complete")
    write_json_atomic(plan_path, payload)
    append_sync_report(report_path, payload)
    return payload


def _apply_queue_action(automation: KhanKidsAutomation, action: QueueAction) -> ActionResult:
    if action.kind == "remove":
        return automation.unassign(action.title, action.variant)
    return automation.assign(action.grade, action.title, action.variant)


def _assignment_keys(snapshot: AssignmentSnapshot) -> set[tuple[str, str]]:
    return {(row.title, row.variant) for row in snapshot.rows}


def _update_operation_journal(
    payload: dict[str, object],
    action: QueueAction,
    *,
    state: str,
    verified_queue: set[tuple[str, str]] | None = None,
) -> None:
    journal = payload.get("operation_journal")
    if not isinstance(journal, dict):
        raise AutomationError("Operation journal is unavailable")
    operations = journal.get("operations")
    if not isinstance(operations, list):
        raise AutomationError("Operation journal has invalid operations")
    matches = [
        operation
        for operation in operations
        if isinstance(operation, dict)
        and operation.get("kind") == action.kind
        and operation.get("title") == action.title
        and operation.get("variant") == action.variant
    ]
    if len(matches) != 1:
        raise AutomationError(f"Operation journal does not uniquely identify {action.key!r}")
    matches[0]["state"] = state
    matches[0][f"{state}_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if verified_queue is not None:
        matches[0]["verified_queue_sha256"] = _key_set_digest(verified_queue)
        matches[0]["verified_queue_count"] = len(verified_queue)


def _set_journal_status(payload: dict[str, object], status: str) -> None:
    journal = payload.get("operation_journal")
    if isinstance(journal, dict):
        journal["status"] = status


def _queue_recovery_payload(
    live_keys: set[tuple[str, str]], desired_keys: set[tuple[str, str]]
) -> dict[str, object]:
    return {
        "status": "captured",
        "live_count": len(live_keys),
        "live_assignments": [
            {"title": title, "variant": variant} for title, variant in sorted(live_keys)
        ],
        "missing_assignments": [
            {"title": title, "variant": variant}
            for title, variant in sorted(desired_keys - live_keys)
        ],
        "unexpected_assignments": [
            {"title": title, "variant": variant}
            for title, variant in sorted(live_keys - desired_keys)
        ],
    }


def _pre_apply_interruption_payload(
    *,
    args: argparse.Namespace,
    curriculum: ReadingCurriculum,
    device: AndroidDevice,
    history_cache: HistoryCache,
    timing: TimingRecorder,
    error: Exception,
) -> dict[str, object]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "version": PLAN_VERSION,
        "status": "interrupted",
        "generated_at": now,
        "interrupted_at": now,
        "student": args.student,
        **({"run_id": args.run_id} if getattr(args, "run_id", None) else {}),
        "path_id": curriculum.path_id,
        "new_attempt_records": 0,
        "new_attempts": [],
        "score_controls_read": [],
        "observed_assignments": [],
        "desired_assignments": [],
        "stretch_assignments": [],
        "actions": [],
        "applied": [],
        "score_evidence": [],
        "track_states": [],
        "active_quarantines": [],
        "error": str(error),
        "recovery": {"status": "unavailable", "error": "workflow stopped before mutation"},
        "performance": {
            "backend": device.ui_backend_name,
            "cache_hits": history_cache.hits,
            "cache_misses": history_cache.misses,
            **timing.snapshot(),
        },
    }


def _record_applied_action(
    *,
    result: ActionResult,
    action: QueueAction,
    actions_path: Path,
    student: str,
    action_date: date,
    applied: list[dict[str, str]],
) -> None:
    record_action(
        actions_path,
        action_date=action_date,
        student=student,
        action=result.action,
        title=result.title,
        variant=result.variant,
        reason=action.reason,
        result=result.result,
    )
    applied.append({"action": result.action, "title": result.title, "variant": result.variant})


def _track_payload(state: TrackState) -> dict[str, object]:
    decision = state.decision
    return {
        "id": state.track_id,
        "complete": state.complete,
        "unlocked": state.unlocked,
        "next": state.next_activity.as_dict() if state.next_activity else None,
        "status": decision.status.value if decision else None,
        "scores": list(decision.scores) if decision else [],
        "reason": decision.reason if decision else "track complete",
    }


def _score_evidence_payload(
    title: str,
    variant: str,
    scores: tuple[int, ...],
    *,
    mastered: bool = False,
) -> dict[str, object]:
    decision = evaluate_mastery(scores)
    if mastered and decision.status.value != "mastered":
        reason = "mastery was preserved from a previously verified assignment action"
        status = "mastered"
    else:
        reason = decision.reason
        status = decision.status.value
    return {
        "title": title,
        "variant": variant,
        "scores": list(decision.scores),
        "status": status,
        "reason": reason,
    }


def _summary(
    payload: dict[str, object], *, plan_path: Path, report_path: Path
) -> dict[str, object]:
    return {
        "dashboard_report": build_dashboard_report(payload),
        "status": payload["status"],
        "student": payload["student"],
        "new_attempt_records": payload.get("new_attempt_records", 0),
        "desired_count": len(payload["desired_assignments"]),
        "stretch_count": len(payload.get("stretch_assignments", [])),
        "action_count": len(payload["actions"]),
        "actions": payload["actions"],
        "plan": str(plan_path),
        "report": str(report_path),
    }


def _read_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise AutomationError(f"Expected a JSON object in {path}")
    return payload


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quarantine_digest(records: tuple[LessonQuarantine, ...]) -> str:
    canonical = json.dumps(
        [record.as_dict() for record in records], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _mastery_digest(keys: set[tuple[str, str]]) -> str:
    return _key_set_digest(keys)


def _key_set_digest(keys: set[tuple[str, str]]) -> str:
    canonical = json.dumps(sorted(keys), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _history_lookup_for_run(args: argparse.Namespace, history_cache: HistoryCache):
    """Mastery syncs must open every available live score control."""
    if args.sync or args.full_score_scan:
        return None
    return history_cache.lookup


def cli() -> None:
    run_id = _argument_value("--run-id") or new_run_id()
    try:
        with exclusive_workflow_lock(WORKFLOW_LOCK_PATH), ProgressReporter(sys.stderr) as progress:
            main(progress, run_id=run_id)
    except Exception as error:
        incident_line = ""
        interrupted_payload = (
            error.payload
            if isinstance(error, MasterySyncInterrupted)
            else _interrupted_payload_from_arguments()
        )
        if interrupted_payload is not None:
            interrupted_payload.setdefault("run_id", run_id)
            with suppress(Exception):
                record_checkin(Path.cwd(), interrupted_payload)
        incident_error = error.cause if isinstance(error, MasterySyncInterrupted) else error
        with suppress(Exception):
            DiagnosticRun(
                Path.cwd(),
                run_id,
                student=public_student(_argument_value("--student") or "unknown"),
            ).record_failure(
                incident_error,
                payload=interrupted_payload,
            )
        if "--sync" in sys.argv:
            try:
                incident_id = append_failed_sync_incident(
                    INCIDENT_LOG_PATH,
                    student=public_student(_argument_value("--student") or "unknown"),
                    error=incident_error,
                    payload=interrupted_payload,
                    run_id=run_id,
                )
                incident_line = f"\nIncident recorded: {incident_id}\n"
            except Exception as incident_error:
                incident_line = (
                    "\nWARNING: automatic incident recording also failed: "
                    f"{type(incident_error).__name__}\n"
                )
        if interrupted_payload is not None:
            detail = f"{render_terminal_summary(interrupted_payload)}\n\nFAILURE: {incident_error}"
            if "--json" in sys.argv:
                print(
                    json.dumps(
                        {
                            "dashboard_report": build_dashboard_report(interrupted_payload),
                            "error": str(incident_error),
                        }
                    )
                )
        else:
            detail = (
                "Khan Mastery Sync — FAILED\n"
                "==========================\n"
                f"Error: {error}\n\n"
                "The workflow stopped before a recoverable live queue was recorded."
            )
        print(f"{detail}{incident_line}", file=sys.stderr)
        raise SystemExit(2) from None


def _argument_value(option: str) -> str | None:
    try:
        index = sys.argv.index(option)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _interrupted_payload_from_arguments() -> dict[str, object] | None:
    student = _argument_value("--student")
    if student is None:
        return None
    student = public_student(student)
    selected = _argument_value("--apply-plan") or _argument_value("--plan")
    path = (
        Path(selected)
        if selected
        else Path(f"private/{student.casefold().replace(' ', '-')}-reading-plan.json")
    )
    try:
        payload = _read_object(path)
    except (AutomationError, OSError, json.JSONDecodeError):
        return None
    return payload if payload.get("status") == "interrupted" else None


if __name__ == "__main__":
    cli()
