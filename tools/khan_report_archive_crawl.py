#!/usr/bin/env python3
"""Read-only crawl of Khan Kids All Progress, optionally including score dialogs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from khan_kids.adb import AndroidDevice, AutomationError, prepare_capture_workspace, run_command
from khan_kids.automation import KhanKidsAutomation
from khan_kids.constants import GRADE_NAMES, GRADE_SLUGS
from khan_kids.launcher import ensure_khan_kids_open, read_local_secrets
from khan_kids.report_archive import (
    REPORT_SUBJECTS,
    assignment_dialog_close_rect,
    parse_progress_history,
    progress_history_close_rect,
    rect_from_list,
    report_filter_value,
    report_outline_rows,
    report_rows,
)
from khan_kids.ui import near, text_set, visible_nodes


def has_disclosure(image: Path, row: dict[str, object]) -> bool:
    x1, y1, _, y2 = (int(value) for value in row["bounds"])  # type: ignore[union-attr]
    pixels = run_command(
        ["magick", str(image), "-crop", f"35x{max(12, y2 - y1 - 10)}+{x1 - 40}+{y1 + 5}", "txt:-"],
        timeout=15,
        capture=True,
    ).decode("utf-8", errors="replace")
    dark_pixels = sum(
        1
        for match in re.finditer(r"srgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", pixels)
        if all(
            int(value) < limit for value, limit in zip(match.groups(), (180, 190, 205), strict=True)
        )
    )
    return dark_pixels >= 8


def collapsed_rows(rows: list[dict[str, object]], image: Path) -> list[dict[str, object]]:
    result = []
    for index, row in enumerate(rows):
        x, y = int(row["x"]), int(row["y"])
        if not (120 <= x <= 145 or 185 <= x <= 215) or y > 1430:
            continue
        if not has_disclosure(image, row):
            continue
        following = next(
            (candidate for candidate in rows[index + 1 :] if int(candidate["y"]) > y), None
        )
        if following is not None and int(following["x"]) <= x:
            result.append(row)
    return result


def expand_visible(
    device: AndroidDevice, scratch: Path, not_expandable: set[str]
) -> tuple[ET.Element, list[dict[str, object]]]:
    ignored: set[tuple[str, int]] = set()
    for _ in range(40):
        root = device.dump(scratch / "expansion.xml")
        if "Class Report: All Progress" not in text_set(root):
            raise AutomationError("Expected an All Progress report")
        rows = report_outline_rows(root)
        image = scratch / "expansion.png"
        device.screenshot(image)
        targets = [
            row
            for row in collapsed_rows(rows, image)
            if (str(row["text"]), int(row["y"])) not in ignored
            and str(row["text"]) not in not_expandable
        ]
        if not targets:
            return root, rows
        before = tuple((row["text"], tuple(row["bounds"])) for row in rows)
        safe_domains = [row for row in targets if int(row["x"]) <= 145]
        selected = safe_domains or targets
        for row in sorted(selected, key=lambda item: int(item["y"]), reverse=True):
            bounds = row["bounds"]
            device.tap(int(row["x"]) - 27, (int(bounds[1]) + int(bounds[3])) // 2)
            if safe_domains:
                time.sleep(0.12)
                continue
            time.sleep(0.7)
            candidate = device.hierarchy()
            if any(item.text.startswith("Assign\n") for item in visible_nodes(candidate)):
                device.tap_rect(assignment_dialog_close_rect(candidate))
                not_expandable.add(str(row["text"]))
                time.sleep(0.5)
        time.sleep(0.5)
        after_root = device.dump(scratch / "expansion-check.xml")
        if any(item.text.startswith("Assign\n") for item in visible_nodes(after_root)):
            device.tap_rect(assignment_dialog_close_rect(after_root))
            time.sleep(1)
            not_expandable.add(str(selected[0]["text"]))
            continue
        after_rows = report_outline_rows(after_root)
        after = tuple((row["text"], tuple(row["bounds"])) for row in after_rows)
        if after == before:
            ignored.update((str(row["text"]), int(row["y"])) for row in targets)
        else:
            ignored.clear()
    raise AutomationError("Visible rows did not settle after expansion")


def close_skills_dialog(
    device: AndroidDevice, student: str, *, root: ET.Element | None = None
) -> None:
    root = root if root is not None else device.hierarchy()
    device.tap_rect(progress_history_close_rect(root, student))
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        root = device.hierarchy()
        headings = {f"{student}'s Skills Scores", f"{student}'s Lesson Scores"}
        if "Class Report: All Progress" in text_set(root) and not headings & text_set(root):
            return
        time.sleep(0.1)
    raise AutomationError("Skills Scores dialog did not close")


def capture_histories_on_page(
    device: AndroidDevice,
    rows: list[dict[str, object]],
    *,
    student: str,
    captured_on: date,
    seen: set[tuple[str, str]],
    histories: list[dict[str, object]],
    unavailable: list[dict[str, object]],
) -> None:
    for row in rows:
        if not near(int(row["x"]), 200):
            continue
        result = row["results"][student]  # type: ignore[index]
        bounds = row["result_bounds"][student]  # type: ignore[index]
        if result["display"] is None or bounds is None:  # type: ignore[index]
            continue
        summary_key = (str(row["text"]), str(result["display"]))  # type: ignore[index]
        if summary_key in seen:
            continue
        seen.add(summary_key)
        device.tap_rect(rect_from_list(bounds))
        deadline = time.monotonic() + 3
        modal: ET.Element | None = None
        while time.monotonic() < deadline:
            candidate = device.hierarchy()
            headings = {f"{student}'s Skills Scores", f"{student}'s Lesson Scores"}
            if headings & text_set(candidate):
                modal = candidate
                break
            time.sleep(0.1)
        if modal is None:
            unavailable.append(
                {
                    "lesson_title": row["text"],
                    "summary_display": result["display"],  # type: ignore[index]
                    "reason": "score cell did not expose a Skills Scores dialog",
                }
            )
            continue
        history = parse_progress_history(modal, student, captured_on=captured_on)
        history["summary_display"] = result["display"]  # type: ignore[index]
        histories[:] = [
            prior
            for prior in histories
            if not (
                prior.get("lesson_title") == history["lesson_title"]
                and prior.get("summary_display") == history["summary_display"]
                and not prior.get("attempts")
            )
        ]
        histories.append(history)
        close_skills_dialog(device, student, root=modal)


def crawl_report(
    device: AndroidDevice,
    destination: Path,
    *,
    student: str,
    roster: tuple[str, ...],
    captured_on: date,
    capture_histories: bool,
    repair_empty_histories: bool,
) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    scratch = destination.parent.parent / ".scratch"
    pages: list[dict[str, object]] = []
    prior_manifest_path = destination / "manifest.json"
    prior_manifest = (
        json.loads(prior_manifest_path.read_text()) if prior_manifest_path.exists() else {}
    )
    histories: list[dict[str, object]] = list(prior_manifest.get("histories", []))
    unavailable: list[dict[str, object]] = list(prior_manifest.get("history_unavailable", []))
    seen_histories: set[tuple[str, str]] = {
        (str(history["lesson_title"]), str(history.get("summary_display")))
        for history in histories
        if not repair_empty_histories or history.get("attempts")
    }
    prior_signature = None
    not_expandable: set[str] = set()
    for page_number in range(300):
        root, _ = expand_visible(device, scratch, not_expandable)
        root = device.dump(destination / f"page-{page_number:03d}.xml")
        device.screenshot(destination / f"page-{page_number:03d}.png")
        rows = report_rows(root, roster)
        signature = tuple((row["text"], tuple(row["bounds"])) for row in rows)
        if signature == prior_signature:
            (destination / f"page-{page_number:03d}.xml").unlink(missing_ok=True)
            (destination / f"page-{page_number:03d}.png").unlink(missing_ok=True)
            break
        pages.append({"page": page_number, "rows": rows})
        if capture_histories:
            capture_histories_on_page(
                device,
                rows,
                student=student,
                captured_on=captured_on,
                seen=seen_histories,
                histories=histories,
                unavailable=unavailable,
            )
        manifest = {
            "filter": report_filter_value(root),
            "student": student,
            "roster": list(roster),
            "captured_on": captured_on.isoformat(),
            "pages": pages,
            "histories": histories,
            "history_unavailable": unavailable,
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        prior_signature = signature
        device.swipe(850, 1380, 850, 680)
        time.sleep(1)
    else:
        raise AutomationError(f"Did not reach report bottom for {report_filter_value(root)}")
    return {
        "filter": report_filter_value(root),
        "page_count": len(pages),
        "history_dialogs": len(histories),
        "history_unavailable": len(unavailable),
    }


def load_parent_password(path: Path) -> str:
    payload = json.loads(path.read_text())
    password = payload.get("khan_parent_password")
    if not isinstance(password, str) or not password:
        raise ValueError(f"Missing khan_parent_password in {path}")
    return password


def report_matrix(
    selected_grades: set[str], selected_subjects: list[str]
) -> list[tuple[str, str, str]]:
    """Return every distinct report Khan exposes for the requested filters."""
    reports = []
    for subject_slug in selected_subjects:
        if subject_slug == "books":
            reports.append((subject_slug, "Kindergarten", "all-ages"))
            continue
        if subject_slug == "videos":
            if selected_grades & set(GRADE_SLUGS[:4]):
                reports.append((subject_slug, "Kindergarten", "k-pre-k"))
            if "1st-grade" in selected_grades:
                reports.append((subject_slug, "1st Grade", "1st-grade"))
            if "2nd-grade" in selected_grades:
                reports.append((subject_slug, "2nd Grade", "2nd-grade"))
            continue
        reports.extend(
            (subject_slug, grade, grade_slug)
            for grade, grade_slug in zip(GRADE_NAMES, GRADE_SLUGS, strict=True)
            if grade_slug in selected_grades
        )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--student", required=True)
    parser.add_argument("--roster", action="append")
    parser.add_argument("--forbid-student", action="append", default=[])
    parser.add_argument("--secrets-file", type=Path, required=True)
    parser.add_argument("--device-secrets-file", type=Path, default=Path(".secrets.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--grade", choices=GRADE_SLUGS, action="append")
    parser.add_argument("--subject", choices=REPORT_SUBJECTS, action="append")
    parser.add_argument("--capture-histories", action="store_true")
    parser.add_argument(
        "--repair-empty-histories",
        action="store_true",
        help="reopen only histories previously captured with no parsed attempts",
    )
    parser.add_argument("--captured-on", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--ui-backend",
        choices=("uiautomator2", "legacy-adb"),
        default="uiautomator2",
    )
    args = parser.parse_args()

    if args.repair_empty_histories and not args.capture_histories:
        parser.error("--repair-empty-histories requires --capture-histories")

    roster = tuple(args.roster or [args.student])
    if args.student not in roster:
        parser.error("--student must be present in --roster")
    password = load_parent_password(args.secrets_file)
    device_secrets = read_local_secrets(args.device_secrets_file)
    device, output, scratch = prepare_capture_workspace(args.serial, args.output)
    automation = KhanKidsAutomation(
        device,
        student=args.student,
        roster=roster,
        forbidden_students=tuple(args.forbid_student),
        scratch=scratch,
        parent_password_provider=lambda: password,
    )
    selected_grades = set(args.grade or GRADE_SLUGS)
    selected_subjects = args.subject or list(REPORT_SUBJECTS)
    summary_path = output / "crawl-summary.json"
    summaries = json.loads(summary_path.read_text()) if summary_path.exists() else []
    completed_reports = {
        (str(summary["subject_slug"]), str(summary["grade_slug"])) for summary in summaries
    }

    def needs_empty_history_repair(subject_slug: str, grade_slug: str) -> bool:
        manifest_path = output / subject_slug / grade_slug / "manifest.json"
        if not manifest_path.exists():
            return False
        manifest = json.loads(manifest_path.read_text())
        return any(not history.get("attempts") for history in manifest.get("histories", []))

    started = datetime.now().astimezone()
    with device.awake_session():
        ensure_khan_kids_open(
            device,
            pin_provider=lambda: device_secrets.android_pin,
            fresh_start=True,
        )
        automation.ensure_all_progress_report()
        device.enable_ui_backend(args.ui_backend)
        for subject_slug, grade, grade_slug in report_matrix(selected_grades, selected_subjects):
            subject = REPORT_SUBJECTS[subject_slug]
            completed = (subject_slug, grade_slug) in completed_reports
            if completed and not (
                args.repair_empty_histories and needs_empty_history_repair(subject_slug, grade_slug)
            ):
                continue
            automation.select_grade_subject(grade, subject)
            summary = {
                "subject_slug": subject_slug,
                "grade_slug": grade_slug,
                **crawl_report(
                    device,
                    output / subject_slug / grade_slug,
                    student=args.student,
                    roster=roster,
                    captured_on=args.captured_on,
                    capture_histories=args.capture_histories,
                    repair_empty_histories=args.repair_empty_histories,
                ),
            }
            summaries = [
                prior
                for prior in summaries
                if (
                    str(prior["subject_slug"]),
                    str(prior["grade_slug"]),
                )
                != (subject_slug, grade_slug)
            ]
            summaries.append(summary)
            summary_path.write_text(json.dumps(summaries, indent=2) + "\n")
            print(json.dumps(summary), flush=True)
        automation.return_to_profile_chooser()
    finished = datetime.now().astimezone()
    run_manifest = {
        "student": args.student,
        "roster": list(roster),
        "forbidden_students": args.forbid_student,
        "capture_started_at": started.isoformat(),
        "capture_finished_at": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "subjects": selected_subjects,
        "grades": [slug for slug in GRADE_SLUGS if slug in selected_grades],
        "grade_independent_subjects": [slug for slug in selected_subjects if slug == "books"],
        "combined_grade_subjects": [slug for slug in selected_subjects if slug == "videos"],
        "reports": summaries,
        "read_only": True,
        "ui_backend": device.ui_backend_name,
    }
    (output / "capture-manifest.json").write_text(json.dumps(run_manifest, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (AutomationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"archive crawl failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
