#!/usr/bin/env python3
"""Reconcile one student's Khan Kids reading queue from a reviewed plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from khan_kids.adb import AndroidDevice, AutomationError
from khan_kids.automation import ActionResult, KhanKidsAutomation
from khan_kids.catalog import CatalogIndex
from khan_kids.curriculum import Activity, ReadingCurriculum
from khan_kids.history_cache import HistoryCache
from khan_kids.incidents import append_failed_sync_incident
from khan_kids.launcher import ensure_khan_kids_open, local_secrets_provider
from khan_kids.mastery import evaluate_mastery
from khan_kids.planner import (
    QueueAction,
    QueuePlan,
    TrackState,
    build_queue_plan,
    snapshot_fingerprint,
)
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
from khan_kids.sync_report import (
    append_performance_report,
    append_sync_report,
    render_terminal_summary,
    terminal_color_enabled,
)
from khan_kids.timing import TimingRecorder
from khan_kids.workflow import histories_to_attempt_rows, overlay_live_scores
from khan_kids.workflow_lock import exclusive_workflow_lock

PLAN_VERSION = 3
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
    if len(desired) != curriculum.queue_limit:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="ADB serial, usually IP:port")
    parser.add_argument("--student", required=True)
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
    if sum(bool(value) for value in (args.plan, args.apply_plan, args.sync)) > 1:
        parser.error("--plan, --apply-plan, and --sync are mutually exclusive")
    if args.max_actions < 1:
        parser.error("--max-actions must be positive")

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

    timing = TimingRecorder()
    device = AndroidDevice(args.serial, timing=timing)
    history_cache = HistoryCache.load(cache_path, student=args.student, today=args.today)
    output_payload: dict[str, object]
    teardown_error: AutomationError | None = None
    output_plan_path = args.apply_plan or plan_path
    try:
        with timing.span("startup.connected"):
            device.assert_connected()
        with (
            device.awake_session(),
            tempfile.TemporaryDirectory(prefix="khan-reading-") as temporary,
        ):
            credentials = local_secrets_provider(args.secrets_file)
            with timing.span("startup.launch"):
                ensure_khan_kids_open(
                    device,
                    pin_provider=lambda: credentials().android_pin,
                    fresh_start=True,
                )
            device.enable_ui_backend(args.ui_backend)
            automation = KhanKidsAutomation(
                device,
                student=args.student,
                roster=catalog.roster,
                scratch=Path(temporary),
                parent_password_provider=lambda: credentials().khan_parent_password,
                history_lookup=_history_lookup_for_run(args, history_cache),
            )
            with timing.span("phase.review_assignments"):
                snapshot = automation.scan_assignments(
                    today=args.today, include_score_histories=True
                )
            history_cache.update(snapshot.rows, snapshot.histories)
            history_cache.save()
            if args.apply_plan:
                assert reviewed_payload is not None
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
                output_payload["teardown"] = {
                    "status": "failed",
                    "error": str(error),
                }
                teardown_error = AutomationError(
                    "Teardown failed after the sync outcome "
                    f"{output_payload['status']!r} had already been saved: {error}"
                )
    except MasterySyncInterrupted:
        raise
    except Exception as error:
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
    output_payload["performance"] = {
        "backend": device.ui_backend_name,
        "cache_hits": history_cache.hits,
        "cache_misses": history_cache.misses,
        **timing_snapshot,
    }
    write_json_atomic(output_plan_path, output_payload)
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
        preferred_grades = {
            key: activity.grade for key, activity in curriculum.activities_by_key.items()
        }
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
            score_scan_mode=(
                "live_all_available" if args.sync or args.full_score_scan else "cache_eligible"
            ),
        )
        write_json_atomic(plan_path, payload)
    if len(queue_plan.desired) != curriculum.queue_limit:
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
    desired, actions = validate_reviewed_plan(
        payload,
        student=args.student,
        snapshot=snapshot,
        catalog_path=args.catalog,
        curriculum_path=args.curriculum,
        curriculum=curriculum,
        active_quarantines=active_quarantines,
        mastered_keys=mastered_keys,
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
            if missing and len(current_keys) < curriculum.queue_limit:
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
                raise AutomationError("Desired assignments cannot fit without a validated removal")

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
                write_json_atomic(plan_path, payload)

        with automation.device.timing.span("phase.fixed_point_verify"):
            fixed_point_snapshot = automation.scan_assignments(
                today=args.today, include_score_histories=False
            )
        fixed_point_keys = _assignment_keys(fixed_point_snapshot)
        if fixed_point_keys != desired_keys or len(fixed_point_keys) != curriculum.queue_limit:
            raise AutomationError("Fixed-point verification did not match the desired queue")
    except Exception as error:
        recovery: dict[str, object]
        try:
            with automation.device.timing.span("phase.interruption_reconcile"):
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
    try:
        with exclusive_workflow_lock(WORKFLOW_LOCK_PATH):
            main()
    except Exception as error:
        incident_line = ""
        interrupted_payload = (
            error.payload
            if isinstance(error, MasterySyncInterrupted)
            else _interrupted_payload_from_arguments()
        )
        incident_error = error.cause if isinstance(error, MasterySyncInterrupted) else error
        if "--sync" in sys.argv:
            try:
                incident_id = append_failed_sync_incident(
                    INCIDENT_LOG_PATH,
                    student=_argument_value("--student") or "unknown",
                    error=incident_error,
                    payload=interrupted_payload,
                )
                incident_line = f"\nIncident recorded: {incident_id}\n"
            except Exception as incident_error:
                incident_line = (
                    "\nWARNING: automatic incident recording also failed: "
                    f"{type(incident_error).__name__}\n"
                )
        if interrupted_payload is not None:
            detail = f"{render_terminal_summary(interrupted_payload)}\n\nFAILURE: {incident_error}"
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
