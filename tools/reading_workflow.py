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
from khan_kids.quarantine import LessonQuarantine, read_active_quarantines
from khan_kids.records import (
    ATTEMPT_FIELDS,
    ATTEMPT_ID_FIELDS,
    append_unique_rows_with_records,
    read_attempt_scores,
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

PLAN_VERSION = 2
INCIDENT_LOG_PATH = Path("INCIDENTS.md")


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
    score_scan_mode: str = "cache_eligible",
) -> dict[str, object]:
    stretch_keys = {
        activity.key for stretch in curriculum.stretch_pool for activity in stretch.activities
    }
    score_map = scores or {}
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
            _score_evidence_payload(title, variant, score_map.get((title, variant), ()))
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

    raw_desired = payload.get("desired_assignments")
    raw_actions = payload.get("actions")
    if not isinstance(raw_desired, list) or not all(isinstance(item, dict) for item in raw_desired):
        raise AutomationError("Reviewed plan has invalid desired assignments")
    if not isinstance(raw_actions, list) or not all(isinstance(item, dict) for item in raw_actions):
        raise AutomationError("Reviewed plan has invalid actions")
    desired = tuple(Activity.from_dict(item) for item in raw_desired)
    actions = tuple(QueueAction.from_dict(item) for item in raw_actions)
    permitted = curriculum.activities_by_key
    if len(desired) > curriculum.queue_limit:
        raise AutomationError("Reviewed plan exceeds the curriculum queue limit")
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
    output_plan_path = args.apply_plan or plan_path
    with timing.span("startup.connected"):
        device.assert_connected()
    with device.awake_session(), tempfile.TemporaryDirectory(prefix="khan-reading-") as temporary:
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
            snapshot = automation.scan_assignments(today=args.today, include_score_histories=True)
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
            )

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
        )
        scores = overlay_live_scores(
            read_attempt_scores(attempts_path, args.student), snapshot.histories
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
            score_scan_mode=(
                "live_all_available" if args.sync or args.full_score_scan else "cache_eligible"
            ),
        )
        write_json_atomic(plan_path, payload)
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
    )
    if len(actions) > args.max_actions:
        raise AutomationError(
            f"Reviewed plan contains {len(actions)} actions; limit is {args.max_actions}"
        )
    applied: list[dict[str, str]] = []
    try:
        removals = {action.key: action for action in actions if action.kind == "remove"}
        with automation.device.timing.span("phase.bulk_remove"):
            for result in automation.unassign_many(removals):
                action = removals[(result.title, result.variant)]
                _record_applied_action(
                    result=result,
                    action=action,
                    actions_path=actions_path,
                    student=args.student,
                    action_date=args.today,
                    applied=applied,
                )
        additions = sorted(
            (action for action in actions if action.kind == "add"),
            key=lambda action: catalog.order_key(action.grade, action.title),
        )
        additions_by_key = {action.key: action for action in additions}
        assignment_specs = tuple(
            (action.grade, action.title, action.variant) for action in additions
        )
        with automation.device.timing.span("phase.batch_add"):
            for result in automation.assign_many(assignment_specs):
                action = additions_by_key[(result.title, result.variant)]
                _record_applied_action(
                    result=result,
                    action=action,
                    actions_path=actions_path,
                    student=args.student,
                    action_date=args.today,
                    applied=applied,
                )

        with automation.device.timing.span("phase.final_verify"):
            final_snapshot = automation.scan_assignments(
                today=args.today, include_score_histories=False
            )
        final_keys = {(row.title, row.variant) for row in final_snapshot.rows}
        desired_keys = {activity.key for activity in desired}
        if final_keys != desired_keys:
            raise AutomationError("Post-apply verification did not match the desired queue")
    except Exception as error:
        payload["status"] = "interrupted"
        payload["interrupted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        payload["error"] = str(error)
        payload["applied"] = applied
        write_json_atomic(plan_path, payload)
        append_sync_report(report_path, payload)
        raise
    payload["status"] = "applied"
    payload["applied_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["applied"] = applied
    write_json_atomic(plan_path, payload)
    append_sync_report(report_path, payload)
    return payload


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
) -> dict[str, object]:
    decision = evaluate_mastery(scores)
    return {
        "title": title,
        "variant": variant,
        "scores": list(decision.scores),
        "status": decision.status.value,
        "reason": decision.reason,
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


def _history_lookup_for_run(args: argparse.Namespace, history_cache: HistoryCache):
    """Mastery syncs must open every available live score control."""
    if args.sync or args.full_score_scan:
        return None
    return history_cache.lookup


def cli() -> None:
    try:
        main()
    except Exception as error:
        incident_line = ""
        if "--sync" in sys.argv:
            try:
                incident_id = append_failed_sync_incident(
                    INCIDENT_LOG_PATH,
                    student=_argument_value("--student") or "unknown",
                    error=error,
                )
                incident_line = f"\nIncident recorded: {incident_id}\n"
            except Exception as incident_error:
                incident_line = (
                    "\nWARNING: automatic incident recording also failed: "
                    f"{type(incident_error).__name__}\n"
                )
        print(
            "Khan Mastery Sync — FAILED\n"
            "==========================\n"
            f"Error: {error}\n\n"
            "The workflow stopped. Review the sync log for any actions completed "
            f"before the interruption.{incident_line}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


def _argument_value(option: str) -> str | None:
    try:
        index = sys.argv.index(option)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


if __name__ == "__main__":
    cli()
