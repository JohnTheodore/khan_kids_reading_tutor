#!/usr/bin/env python3
"""Build normalized records from one or more read-only All Progress crawls."""

from __future__ import annotations

import argparse
import copy
import csv
import json
from collections import defaultdict
from pathlib import Path

from khan_kids.constants import GRADE_SLUGS, normalize_report_grade_label
from khan_kids.report_archive import REPORT_SUBJECTS, parse_page
from khan_kids.ui import VARIANT_ORDER, near


def stitched_title_rows(
    report_dir: Path, page_count: int, students: tuple[str, ...]
) -> list[dict[str, object]]:
    """Remove scroll overlap while preserving real repeated title placements."""
    stitched: list[dict[str, object]] = []

    def signature(row: dict[str, object]) -> tuple[object, ...]:
        return (
            row["text"],
            tuple(
                (student, row["results"][student]["display"])  # type: ignore[index]
                for student in students
            ),
        )

    for page_number in range(page_count):
        page = [
            row
            for row in parse_page(report_dir / f"page-{page_number:03d}.xml", students)
            if near(int(row["x"]), 200)
        ]
        page_signatures = [signature(row) for row in page]
        stitched_signatures = [signature(row) for row in stitched]
        if any(
            stitched_signatures[start : start + len(page)] == page_signatures
            for start in range(len(stitched) - len(page) + 1)
        ):
            continue
        overlap = 0
        for size in range(min(len(stitched), len(page)), 0, -1):
            if stitched_signatures[-size:] == page_signatures[:size]:
                overlap = size
                break
        stitched.extend(page[overlap:])
    return stitched


def merge_result(
    destination: dict[str, object],
    source: dict[str, object],
    conflicts: list[tuple[str, object, object]],
    students: tuple[str, ...],
) -> None:
    for student in students:
        old, new = destination[student], source[student]  # type: ignore[index]
        if old["display"] is None and new["display"] is not None:
            destination[student] = new
        elif (
            old["display"] is not None
            and new["display"] is not None
            and old["display"] != new["display"]
        ):
            conflicts.append((student, old["display"], new["display"]))


def build_report(
    report_dir: Path, students: tuple[str, ...]
) -> tuple[dict[str, object], list[tuple[str, object, object]]]:
    manifest = json.loads((report_dir / "manifest.json").read_text())
    page_count = len(manifest["pages"])
    records: dict[tuple[str | None, str | None, str], dict[str, object]] = {}
    title_keys: dict[str, list[tuple[str | None, str | None, str]]] = defaultdict(list)
    group_domains: dict[str, set[str]] = defaultdict(set)
    conflicts: list[tuple[str, object, object]] = []
    global_domain: str | None = None
    global_group: str | None = None
    current_key: tuple[str | None, str | None, str] | None = None
    reported_total = None

    for page_number in range(page_count):
        rows = parse_page(report_dir / f"page-{page_number:03d}.xml", students)
        domain: str | None = None
        group: str | None = None
        explicit_domain_seen = False
        page_current = current_key
        title_seen_on_page = False
        for row in rows:
            x, text = int(row["x"]), str(row["text"])
            if near(x, 100):
                if reported_total is None:
                    totals = [
                        value.get("total")
                        for value in row["results"].values()  # type: ignore[union-attr]
                        if value.get("status") == "aggregate_count"
                    ]
                    if totals:
                        reported_total = int(totals[0])
                continue
            if near(x, 133):
                domain, group, page_current = text, None, None
                explicit_domain_seen = True
                continue
            if near(x, 167):
                group, page_current = text, None
                if domain is None:
                    known = group_domains[text]
                    domain = next(iter(known)) if len(known) == 1 else global_domain
                if domain:
                    group_domains[text].add(domain)
                continue
            if near(x, 200):
                title_seen_on_page = True
                known_keys = title_keys[text]
                if group is None and len(known_keys) == 1:
                    key = known_keys[0]
                    domain, group = key[0], key[1]
                else:
                    domain, group = domain or global_domain, group or global_group
                    key = (domain, group, text)
                    if (
                        known_keys
                        and key not in known_keys
                        and not explicit_domain_seen
                        and len(known_keys) == 1
                    ):
                        key = known_keys[0]
                        domain, group = key[0], key[1]
                page_current = key
                if key not in records:
                    records[key] = {
                        "order": len(records) + 1,
                        "domain": key[0],
                        "skill_group": key[1],
                        "title": text,
                        "aggregate_results": row["results"],
                        "activities": {},
                    }
                    title_keys[text].append(key)
                else:
                    merge_result(
                        records[key]["aggregate_results"], row["results"], conflicts, students
                    )  # type: ignore[arg-type]
                continue
            if near(x, 239) and title_seen_on_page and page_current and text in VARIANT_ORDER:
                activities = records[page_current]["activities"]  # type: ignore[index]
                if text not in activities:
                    activities[text] = {"variant": text, "results": row["results"]}
                else:
                    merge_result(activities[text]["results"], row["results"], conflicts, students)
        global_domain = domain or global_domain
        global_group = group or global_group
        current_key = page_current or current_key

    placements = []
    for record in records.values():
        activities = record.pop("activities")
        record["activities"] = [activities[v] for v in VARIANT_ORDER if v in activities]
        record["lesson_type"] = "variant_group" if activities else "direct"
        placements.append(record)
    title_rows = stitched_title_rows(report_dir, page_count, students)
    expected_occurrences: defaultdict[str, int] = defaultdict(int)
    retained_occurrences: defaultdict[str, int] = defaultdict(int)
    for placement in placements:
        retained_occurrences[str(placement["title"])] += 1
    records_by_title: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for placement in placements:
        records_by_title[str(placement["title"])].append(placement)
    for row in title_rows:
        title = str(row["text"])
        expected_occurrences[title] += 1
        if expected_occurrences[title] <= retained_occurrences[title]:
            continue
        if not records_by_title[title]:
            raise RuntimeError(f"Could not reconstruct repeated report placement {title!r}")
        duplicate = copy.deepcopy(records_by_title[title][0])
        duplicate["aggregate_results"] = row["results"]
        duplicate["duplicate_placement_occurrence"] = expected_occurrences[title]
        placements.append(duplicate)
    if reported_total is None:
        raise RuntimeError(f"Could not read reported lesson total for {report_dir}")
    if len(placements) != reported_total:
        raise RuntimeError(
            f"{report_dir}: extracted {len(placements)} placements, report says {reported_total}"
        )
    return (
        {
            "filter": manifest["filter"],
            "capture_pages": page_count,
            "reported_lesson_total": reported_total,
            "lesson_placements": placements,
            "histories": manifest.get("histories", []),
            "history_unavailable": manifest.get("history_unavailable", []),
        },
        conflicts,
    )


def discover_reports(source: Path) -> list[tuple[str, str, Path]]:
    reports = []
    for subject in REPORT_SUBJECTS:
        subject_dir = source / subject
        if not subject_dir.is_dir():
            continue
        for grade in (*GRADE_SLUGS, "all-ages", "k-pre-k"):
            if (subject_dir / grade / "manifest.json").exists():
                reports.append((subject, grade, subject_dir / grade))
    if reports:
        return reports
    return [
        ("ela", grade, source / grade)
        for grade in GRADE_SLUGS
        if (source / grade / "manifest.json").exists()
    ]


def write_outputs(
    reports: list[dict[str, object]], students: tuple[str, ...], output: Path
) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=True)
    inventory_path = output / "lesson-inventory.csv"
    attempts_path = output / "lesson-attempts.csv"
    discrepancy_path = output / "summary-history-discrepancies.csv"
    manifest_path = output / "capture-manifest.json"
    markdown_path = output / "learning-history.md"
    completeness_path = output / "completeness-report.md"

    inventory_rows = []
    attempt_rows = []
    summary_by_title: dict[tuple[str, str, str], str | None] = {}
    unavailable = []
    for report in reports:
        subject, grade = str(report["subject"]), str(report["grade_slug"])
        unavailable.extend(
            {"subject": subject, "grade": grade, **row} for row in report["history_unavailable"]
        )  # type: ignore[arg-type]
        for placement in report["lesson_placements"]:  # type: ignore[union-attr]
            activities = placement["activities"] or [
                {"variant": "Direct", "results": placement["aggregate_results"]}
            ]
            for activity in activities:
                for student in students:
                    result = activity["results"][student]
                    inventory_rows.append(
                        {
                            "student": student,
                            "subject": subject,
                            "grade": grade,
                            "domain": placement["domain"],
                            "skill_group": placement["skill_group"],
                            "lesson_title": placement["title"],
                            "activity_variant": activity["variant"],
                            "result_status": result["status"],
                            "result_display": result["display"],
                            "title_aggregate": placement["aggregate_results"][student]["display"],
                        }
                    )
                    summary_by_title[(subject, grade, placement["title"])] = placement[
                        "aggregate_results"
                    ][student]["display"]
        for history in report["histories"]:  # type: ignore[union-attr]
            occurrences: defaultdict[tuple[object, object, object], int] = defaultdict(int)
            for item in history["attempts"]:
                identity = (item["normalized_date"], item["variant"], item["score_percent"])
                occurrences[identity] += 1
                attempt_rows.append(
                    {
                        "student": history["student"],
                        "subject": subject,
                        "grade": grade,
                        "lesson_title": history["lesson_title"],
                        "curriculum_path": history["curriculum_path"],
                        "activity_variant": item["variant"],
                        "display_date": item["display_date"],
                        "normalized_date": item["normalized_date"],
                        "date_resolution": item["date_resolution"],
                        "score_percent": item["score_percent"],
                        "occurrence": occurrences[identity],
                    }
                )

    discrepancies = []
    history_titles = {(row["subject"], row["grade"], row["lesson_title"]) for row in attempt_rows}
    for key, display in summary_by_title.items():
        if display is not None and key not in history_titles:
            discrepancies.append(
                {
                    "subject": key[0],
                    "grade": key[1],
                    "lesson_title": key[2],
                    "summary": display,
                    "issue": "no detailed history captured",
                }
            )

    _write_csv(inventory_path, inventory_rows)
    _write_csv(attempts_path, attempt_rows)
    _write_csv(discrepancy_path, discrepancies)
    payload = {
        "students": list(students),
        "report_count": len(reports),
        "expected_report_count": 22,
        "inventory_rows": len(inventory_rows),
        "dated_attempt_rows": len(attempt_rows),
        "summary_history_discrepancies": len(discrepancies),
        "history_unavailable": unavailable,
        "reports": reports,
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n")
    lines = [
        f"# {students[0]}’s Khan Kids learning history"
        if len(students) == 1
        else "# Khan Kids learning history",
        "",
        f"- Reports captured: {len(reports)}/22 (18 graded core, 1 all-ages, 3 video bands)",
        f"- Lesson/activity inventory rows: {len(inventory_rows):,}",
        f"- Dated attempt rows exposed by Khan Kids: {len(attempt_rows):,}",
        f"- Summary/history discrepancies: {len(discrepancies):,}",
        "",
        "Dates retain Khan Kids’ displayed value. When the app omits the year, the normalized year is the most recent non-future year whose weekday matches; the inference method is recorded per row.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n")
    completeness_path.write_text(
        "# Capture completeness\n\n"
        f"- Report coverage: {len(reports)}/22 (Books is All Ages; Videos uses 3 bands)\n"
        f"- Detailed-history gaps: {len(discrepancies)}\n"
        f"- Score cells without an exposed dialog: {len(unavailable)}\n\n"
        "A complete archive means every row Khan Kids currently exposes was captured. It cannot prove retention of attempts the service no longer displays.\n"
    )
    return {
        "reports": len(reports),
        "inventory_rows": len(inventory_rows),
        "attempt_rows": len(attempt_rows),
        "discrepancies": len(discrepancies),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--history-source",
        type=Path,
        help="read detailed histories from a separate crawl with matching reports",
    )
    parser.add_argument("--student", action="append")
    parser.add_argument("--output", type=Path, default=Path("private/report-archive.local"))
    args = parser.parse_args()
    students = tuple(args.student or ["Student A", "Student B"])
    history_reports = (
        {
            (subject, grade_slug): path
            for subject, grade_slug, path in discover_reports(args.history_source)
        }
        if args.history_source
        else {}
    )
    reports = []
    for subject, grade_slug, path in discover_reports(args.source):
        report, report_conflicts = build_report(path, students)
        if args.history_source:
            history_path = history_reports.get((subject, grade_slug))
            if history_path is None:
                parser.error(f"history source is missing {subject}/{grade_slug}")
            history_manifest = json.loads((history_path / "manifest.json").read_text())
            report["histories"] = history_manifest.get("histories", [])
            report["history_unavailable"] = history_manifest.get("history_unavailable", [])
        report["subject"] = subject
        report["grade_slug"] = grade_slug
        report["grade"] = normalize_report_grade_label(str(report["filter"]))
        report["repeated_title_score_conflicts"] = report_conflicts
        reports.append(report)
    print(json.dumps(write_outputs(reports, students, args.output)))


if __name__ == "__main__":
    main()
