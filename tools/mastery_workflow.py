#!/usr/bin/env python3
"""Review Khan Kids scores and safely apply mastery promotions for one student.

Start from the logged-in Teacher view or either Class Reports tab. The default
is read-only: it scans score histories, updates the attempt record, and writes a
compact JSON plan. Pass --apply to execute only the plan's mastered transitions.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from khan_kids.adb import AndroidDevice
from khan_kids.automation import KhanKidsAutomation
from khan_kids.catalog import CatalogIndex
from khan_kids.mastery import evaluate_mastery, next_variant
from khan_kids.records import append_unique_rows, write_json_atomic
from khan_kids.reports import ScoreHistory

ATTEMPT_FIELDS = (
    "student",
    "attempt_date",
    "assignment_date",
    "lesson_title",
    "activity_variant",
    "report_grade",
    "domain",
    "skill_group",
    "score_percent",
    "source",
    "captured_at",
)
ACTION_FIELDS = (
    "action_date",
    "student",
    "action",
    "lesson_title",
    "activity_variant",
    "reason",
    "result",
)


def build_plan(
    histories: Iterable[ScoreHistory], catalog: CatalogIndex
) -> tuple[list[dict], list[dict]]:
    attempts: list[dict] = []
    decisions: list[dict] = []
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for history in histories:
        entry = catalog.find(history.title, history.variant, history.curriculum_path)
        chronological = tuple(reversed(history.attempts_newest_first))
        scores = tuple(attempt.score for attempt in chronological)
        decision = evaluate_mastery(scores)
        successor = next_variant(history.variant, entry.variants)
        action = "hold"
        if decision.should_advance:
            action = "promote" if successor else "complete"
        decisions.append(
            {
                "student": history.student,
                "lesson_title": history.title,
                "current_variant": history.variant,
                "scores_chronological": list(scores),
                "status": decision.status.value,
                "reason": decision.reason,
                "grade": entry.grade,
                "domain": entry.domain,
                "skill_group": entry.skill_group,
                "available_variants": list(entry.variants),
                "next_variant": successor,
                "action": action,
            }
        )
        for attempt in chronological:
            attempts.append(
                {
                    "student": history.student,
                    "attempt_date": attempt.attempt_date.isoformat(),
                    "assignment_date": history.assigned_date.isoformat(),
                    "lesson_title": history.title,
                    "activity_variant": history.variant,
                    "report_grade": entry.grade,
                    "domain": entry.domain,
                    "skill_group": entry.skill_group,
                    "score_percent": attempt.score,
                    "source": "Khan Kids Class Reports > Assignments > Lesson Scores",
                    "captured_at": captured_at,
                }
            )
    decisions.sort(key=lambda item: (item["lesson_title"].casefold(), item["current_variant"]))
    return attempts, decisions


def record_action(
    path: Path,
    *,
    action_date: date,
    student: str,
    action: str,
    title: str,
    variant: str,
    reason: str,
    result: str,
) -> None:
    append_unique_rows(
        path,
        ACTION_FIELDS,
        [
            {
                "action_date": action_date.isoformat(),
                "student": student,
                "action": action,
                "lesson_title": title,
                "activity_variant": variant,
                "reason": reason,
                "result": result,
            }
        ],
        identity_fields=("action_date", "student", "action", "lesson_title", "activity_variant"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="ADB serial, usually IP:port")
    parser.add_argument("--student", required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/reading-ela-archive.json"))
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--actions", type=Path)
    parser.add_argument("--plan", type=Path, default=Path("private/mastery-plan.json"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply mastered transitions; without this flag the command is read-only",
    )
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    slug = args.student.casefold().replace(" ", "-")
    attempts_path = args.attempts or Path(f"student-records/{slug}-lesson-attempts.csv")
    actions_path = args.actions or Path(f"student-records/{slug}-assignment-actions.csv")
    catalog = CatalogIndex(args.catalog)
    if args.student not in catalog.roster:
        parser.error(f"{args.student!r} is not in catalog roster {catalog.roster!r}")

    device = AndroidDevice(args.serial)
    device.assert_connected()
    with device.awake_session(), tempfile.TemporaryDirectory(prefix="khan-mastery-") as temporary:
        automation = KhanKidsAutomation(
            device,
            student=args.student,
            roster=catalog.roster,
            scratch=Path(temporary),
        )
        histories = automation.scan_score_histories(today=args.today)
        attempts, decisions = build_plan(histories, catalog)
        appended = append_unique_rows(
            attempts_path,
            ATTEMPT_FIELDS,
            attempts,
            identity_fields=(
                "student",
                "attempt_date",
                "lesson_title",
                "activity_variant",
                "score_percent",
            ),
        )
        payload = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "student": args.student,
            "mode": "apply" if args.apply else "dry-run",
            "new_attempt_records": appended,
            "decisions": decisions,
        }
        write_json_atomic(args.plan, payload)

        applied: list[dict[str, str]] = []
        if args.apply:
            for decision in decisions:
                if decision["action"] not in {"promote", "complete"}:
                    continue
                removed = automation.unassign(decision["lesson_title"], decision["current_variant"])
                record_action(
                    actions_path,
                    action_date=args.today,
                    student=args.student,
                    action=removed.action,
                    title=removed.title,
                    variant=removed.variant,
                    reason=decision["reason"],
                    result=removed.result,
                )
                applied.append(asdict(removed))
                if decision["action"] == "promote":
                    assigned = automation.assign(
                        decision["grade"],
                        decision["lesson_title"],
                        decision["next_variant"],
                    )
                    record_action(
                        actions_path,
                        action_date=args.today,
                        student=args.student,
                        action=assigned.action,
                        title=assigned.title,
                        variant=assigned.variant,
                        reason=f"{decision['current_variant']} mastered",
                        result=assigned.result,
                    )
                    applied.append(asdict(assigned))
            payload["applied"] = applied
            write_json_atomic(args.plan, payload)

    print(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    main()
