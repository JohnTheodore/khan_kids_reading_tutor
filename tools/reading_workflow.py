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
from khan_kids.planner import (
    QueueAction,
    QueuePlan,
    TrackState,
    build_queue_plan,
    snapshot_fingerprint,
)
from khan_kids.records import (
    ATTEMPT_FIELDS,
    ATTEMPT_ID_FIELDS,
    append_unique_rows,
    read_attempt_scores,
    record_action,
    write_json_atomic,
)
from khan_kids.reports import AssignmentSnapshot
from khan_kids.workflow import histories_to_attempt_rows, overlay_live_scores

PLAN_VERSION = 1


def create_plan_payload(
    *,
    student: str,
    snapshot: AssignmentSnapshot,
    plan: QueuePlan,
    catalog_path: Path,
    curriculum_path: Path,
    new_attempt_records: int,
    generated_at: datetime,
) -> dict[str, object]:
    return {
        "version": PLAN_VERSION,
        "status": "review_required",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "student": student,
        "catalog_sha256": _file_digest(catalog_path),
        "curriculum_sha256": _file_digest(curriculum_path),
        "observed_state_sha256": snapshot_fingerprint(snapshot),
        "new_attempt_records": new_attempt_records,
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
        "actions": [action.as_dict() for action in plan.actions],
        "track_states": [_track_payload(state) for state in plan.tracks],
    }


def validate_reviewed_plan(
    payload: dict[str, object],
    *,
    student: str,
    snapshot: AssignmentSnapshot,
    catalog_path: Path,
    curriculum_path: Path,
    curriculum: ReadingCurriculum,
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

    raw_desired = payload.get("desired_assignments")
    raw_actions = payload.get("actions")
    if not isinstance(raw_desired, list) or not all(isinstance(item, dict) for item in raw_desired):
        raise AutomationError("Reviewed plan has invalid desired assignments")
    if not isinstance(raw_actions, list) or not all(isinstance(item, dict) for item in raw_actions):
        raise AutomationError("Reviewed plan has invalid actions")
    desired = tuple(Activity.from_dict(item) for item in raw_desired)
    actions = tuple(QueueAction.from_dict(item) for item in raw_actions)
    permitted = {
        activity.key: activity for track in curriculum.tracks for activity in track.activities
    }
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
    parser.add_argument("--catalog", type=Path, default=Path("data/reading-ela-archive.json"))
    parser.add_argument("--curriculum", type=Path, default=Path("data/reading-curriculum.json"))
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--actions", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--apply-plan", type=Path)
    parser.add_argument("--max-actions", type=int, default=20)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    if args.plan and args.apply_plan:
        parser.error("--plan and --apply-plan are mutually exclusive")
    if args.max_actions < 1:
        parser.error("--max-actions must be positive")

    slug = args.student.casefold().replace(" ", "-")
    attempts_path = args.attempts or Path(f"student-records/{slug}-lesson-attempts.csv")
    actions_path = args.actions or Path(f"student-records/{slug}-assignment-actions.csv")
    plan_path = args.plan or Path(f"private/{slug}-reading-plan.json")
    catalog = CatalogIndex(args.catalog)
    if args.student not in catalog.roster:
        parser.error(f"{args.student!r} is not in catalog roster {catalog.roster!r}")
    curriculum = ReadingCurriculum.load(args.curriculum, catalog)
    reviewed_payload = None
    if args.apply_plan:
        reviewed_payload = _read_object(args.apply_plan)
        _validate_plan_metadata(
            reviewed_payload,
            student=args.student,
            catalog_path=args.catalog,
            curriculum_path=args.curriculum,
        )

    device = AndroidDevice(args.serial)
    device.assert_connected()
    with device.awake_session(), tempfile.TemporaryDirectory(prefix="khan-reading-") as temporary:
        automation = KhanKidsAutomation(
            device,
            student=args.student,
            roster=catalog.roster,
            scratch=Path(temporary),
        )
        snapshot = automation.scan_assignments(today=args.today, include_score_histories=True)
        if args.apply_plan:
            assert reviewed_payload is not None
            _apply_reviewed_plan(
                args=args,
                payload=reviewed_payload,
                snapshot=snapshot,
                automation=automation,
                actions_path=actions_path,
                curriculum=curriculum,
            )
            return

        attempt_rows = histories_to_attempt_rows(snapshot.histories, catalog)
        appended = append_unique_rows(
            attempts_path,
            ATTEMPT_FIELDS,
            attempt_rows,
            identity_fields=ATTEMPT_ID_FIELDS,
        )
        scores = overlay_live_scores(
            read_attempt_scores(attempts_path, args.student), snapshot.histories
        )
        current = {(row.title, row.variant) for row in snapshot.rows}
        queue_plan = build_queue_plan(curriculum, scores, current)
        payload = create_plan_payload(
            student=args.student,
            snapshot=snapshot,
            plan=queue_plan,
            catalog_path=args.catalog,
            curriculum_path=args.curriculum,
            new_attempt_records=appended,
            generated_at=datetime.now().astimezone(),
        )
        write_json_atomic(plan_path, payload)
        print(json.dumps(_summary(payload, plan_path=plan_path), separators=(",", ":")))


def _apply_reviewed_plan(
    *,
    args: argparse.Namespace,
    payload: dict[str, object],
    snapshot: AssignmentSnapshot,
    automation: KhanKidsAutomation,
    actions_path: Path,
    curriculum: ReadingCurriculum,
) -> None:
    desired, actions = validate_reviewed_plan(
        payload,
        student=args.student,
        snapshot=snapshot,
        catalog_path=args.catalog,
        curriculum_path=args.curriculum,
        curriculum=curriculum,
    )
    if len(actions) > args.max_actions:
        raise AutomationError(
            f"Reviewed plan contains {len(actions)} actions; limit is {args.max_actions}"
        )
    applied: list[dict[str, str]] = []
    removals = {action.key: action for action in actions if action.kind == "remove"}
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
    for action in (item for item in actions if item.kind == "add"):
        result = automation.assign(action.grade, action.title, action.variant)
        _record_applied_action(
            result=result,
            action=action,
            actions_path=actions_path,
            student=args.student,
            action_date=args.today,
            applied=applied,
        )

    final_snapshot = automation.scan_assignments(today=args.today, include_score_histories=False)
    final_keys = {(row.title, row.variant) for row in final_snapshot.rows}
    desired_keys = {activity.key for activity in desired}
    if final_keys != desired_keys:
        raise AutomationError("Post-apply verification did not match the desired queue")
    payload["status"] = "applied"
    payload["applied_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["applied"] = applied
    write_json_atomic(args.apply_plan, payload)
    print(json.dumps(_summary(payload, plan_path=args.apply_plan), separators=(",", ":")))


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


def _summary(payload: dict[str, object], *, plan_path: Path) -> dict[str, object]:
    return {
        "status": payload["status"],
        "student": payload["student"],
        "new_attempt_records": payload.get("new_attempt_records", 0),
        "desired_count": len(payload["desired_assignments"]),
        "action_count": len(payload["actions"]),
        "actions": payload["actions"],
        "plan": str(plan_path),
    }


def _read_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise AutomationError(f"Expected a JSON object in {path}")
    return payload


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli() -> None:
    try:
        main()
    except (AutomationError, FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    cli()
