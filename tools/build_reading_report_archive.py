#!/usr/bin/env python3
"""Build the complete ELA archive and student-performance record from reports."""

from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from khan_kids.constants import GRADE_SLUGS, normalize_report_grade_label
from khan_kids.ui import VARIANT_ORDER, node_rect

GRADE_ORDER = GRADE_SLUGS
STUDENTS = ("Student A", "Student B")


def score_value(raw: str | None) -> dict[str, object]:
    if raw is None:
        return {"display": None, "status": "not_attempted"}
    if match := re.fullmatch(r"(\d+)%", raw):
        return {"display": raw, "status": "scored", "percent": int(match.group(1))}
    if match := re.fullmatch(r"(\d+)/(\d+)", raw):
        return {
            "display": raw,
            "status": "aggregate_count",
            "completed": int(match.group(1)),
            "total": int(match.group(2)),
        }
    if raw == "Viewed":
        return {"display": raw, "status": "viewed"}
    return {"display": raw, "status": "other"}


def page_rows(path: Path) -> list[dict[str, object]]:
    root = ET.parse(path).getroot()
    text_nodes = []
    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()
        rect = node_rect(node)
        if text and rect is not None:
            text_nodes.append((text, rect))

    rows = []
    for text, left_box in text_nodes:
        x1, y1, x2, y2 = (
            left_box.left,
            left_box.top,
            left_box.right,
            left_box.bottom,
        )
        if not (385 <= y1 < 1600 and x1 >= 90 and x2 <= 570):
            continue
        displays = {}
        for student, low_x, high_x in (
            ("Student A", 570, 780),
            ("Student B", 780, 1000),
        ):
            values = []
            for candidate, candidate_box in text_nodes:
                candidate_center_y = candidate_box.center[1]
                if (
                    low_x <= candidate_box.left < high_x
                    and y1 <= candidate_center_y <= y2
                    and candidate not in values
                ):
                    values.append(candidate)
            displays[student] = " | ".join(values) if values else None
        rows.append(
            {
                "text": text,
                "x": x1,
                "bounds": left_box.as_list(),
                "results": {student: score_value(displays[student]) for student in STUDENTS},
            }
        )
    unique = {(row["text"], tuple(row["bounds"])): row for row in rows}
    return sorted(unique.values(), key=lambda row: (row["bounds"][1], row["x"]))  # type: ignore[index]


def merge_result(
    destination: dict[str, object], source: dict[str, object], conflicts: list
) -> None:
    for student in STUDENTS:
        old = destination[student]
        new = source[student]
        if old["display"] is None and new["display"] is not None:  # type: ignore[index]
            destination[student] = new
        elif (
            old["display"] is not None  # type: ignore[index]
            and new["display"] is not None  # type: ignore[index]
            and old["display"] != new["display"]  # type: ignore[index]
        ):
            conflicts.append((student, old["display"], new["display"]))  # type: ignore[index]


def build_grade(grade_dir: Path) -> tuple[dict[str, object], list]:
    manifest = json.loads((grade_dir / "manifest.json").read_text())
    page_count = len(manifest["pages"])
    records: dict[tuple[str | None, str | None, str], dict[str, object]] = {}
    title_keys: dict[str, list[tuple[str | None, str | None, str]]] = defaultdict(list)
    group_domains: dict[str, set[str]] = defaultdict(set)
    conflicts = []
    global_domain: str | None = None
    global_group: str | None = None
    current_key: tuple[str | None, str | None, str] | None = None
    grade_label = normalize_report_grade_label(manifest["grade"])
    reported_total = None

    for page_number in range(page_count):
        rows = page_rows(grade_dir / f"page-{page_number:03d}.xml")
        domain: str | None = None
        group: str | None = None
        explicit_domain_seen = False
        page_current = current_key
        title_seen_on_page = False
        for row in rows:
            x = int(row["x"])
            text = str(row["text"])
            if x == 100:
                if reported_total is None:
                    totals = [
                        result.get("total")
                        for result in row["results"].values()  # type: ignore[union-attr]
                        if result.get("status") == "aggregate_count"
                    ]
                    if totals:
                        reported_total = int(totals[0])
                continue
            if x == 133:
                domain, group, page_current = text, None, None
                explicit_domain_seen = True
                continue
            if x in (166, 167):
                group, page_current = text, None
                if domain is None:
                    known_domains = group_domains[text]
                    domain = next(iter(known_domains)) if len(known_domains) == 1 else global_domain
                if domain:
                    group_domains[text].add(domain)
                continue
            if x == 200:
                title_seen_on_page = True
                known_keys = title_keys[text]
                if group is None and len(known_keys) == 1:
                    key = known_keys[0]
                    domain, group = key[0], key[1]
                else:
                    domain = domain or global_domain
                    group = group or global_group
                    key = (domain, group, text)
                    # At an overlapping page prefix, an earlier heading may be
                    # offscreen. Prefer the known placement unless this page has
                    # explicitly entered a new domain (the Age-4 Letters & Words case).
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
                    merge_result(records[key]["aggregate_results"], row["results"], conflicts)  # type: ignore[arg-type]
                continue
            if (
                x == 239
                and title_seen_on_page
                and page_current is not None
                and text in VARIANT_ORDER
            ):
                activity = records[page_current]["activities"]  # type: ignore[index]
                if text not in activity:
                    activity[text] = {"variant": text, "results": row["results"]}
                else:
                    merge_result(activity[text]["results"], row["results"], conflicts)

        if domain is not None:
            global_domain = domain
        if group is not None:
            global_group = group
        if page_current is not None:
            current_key = page_current

    placements = []
    for record in records.values():
        activities = record.pop("activities")
        record["activities"] = [activities[v] for v in VARIANT_ORDER if v in activities]
        record["lesson_type"] = "variant_group" if activities else "direct"
        placements.append(record)

    if reported_total is None:
        raise RuntimeError(f"Could not read reported lesson total for {grade_dir.name}")
    if len(placements) != reported_total:
        raise RuntimeError(
            f"{grade_dir.name}: extracted {len(placements)} placements, report says {reported_total}"
        )
    return (
        {
            "slug": grade_dir.name,
            "grade": grade_label,
            "capture_pages": page_count,
            "reported_lesson_total": reported_total,
            "lesson_placements": placements,
        },
        conflicts,
    )


def result_display(result: dict[str, object]) -> str:
    return str(result["display"]) if result["display"] is not None else "—"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--json", type=Path, default=Path("data/reading-ela-archive.json"))
    parser.add_argument("--markdown", type=Path, default=Path("reading-ela-archive.md"))
    parser.add_argument("--csv", type=Path, default=Path("reading-ela-performance.csv"))
    args = parser.parse_args()

    grades = []
    conflicts = []
    for slug in GRADE_ORDER:
        grade, grade_conflicts = build_grade(args.source / slug)
        grades.append(grade)
        conflicts.extend(grade_conflicts)
    if conflicts:
        raise RuntimeError(f"Conflicting repeated score cells: {conflicts[:10]}")

    all_placements = [p for grade in grades for p in grade["lesson_placements"]]
    activity_count = sum(max(1, len(p["activities"])) for p in all_placements)
    unique_titles = sorted(
        {p["title"] for p in all_placements}, key=lambda title: (title.casefold(), title)
    )
    payload = {
        "source": "Khan Academy Kids Class Reports > All Progress > English Language Arts",
        "captured_on": "2026-09-08",
        "android_app_version": "9.0.1 (versionCode 123)",
        "students": list(STUDENTS),
        "notes": [
            "Results are copied exactly from the report: percentage, Viewed, aggregate count, or null when blank.",
            "A blank report cell is represented as null/not_attempted; it is not interpreted as a zero score.",
            "Lesson placements retain grade and hierarchy. The same title may appear in more than one grade.",
            "For expandable lesson titles, aggregate title results and individual activity-variant results are both retained.",
            "The report provides domain and skill-group context, but no prose lesson descriptions.",
        ],
        "grade_count": len(grades),
        "reported_lesson_placements": len(all_placements),
        "assignable_activity_placements": activity_count,
        "globally_unique_title_strings": len(unique_titles),
        "unique_title_strings": unique_titles,
        "grades": grades,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2) + "\n")

    with args.csv.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "grade",
                "domain",
                "skill_group",
                "lesson_title",
                "activity_variant",
                "Student A",
                "Student B",
                "title_aggregate_Student A",
                "title_aggregate_Student B",
            ]
        )
        for grade in grades:
            for placement in grade["lesson_placements"]:
                activities = placement["activities"] or [
                    {"variant": "Direct", "results": placement["aggregate_results"]}
                ]
                for activity in activities:
                    writer.writerow(
                        [
                            grade["grade"],
                            placement["domain"],
                            placement["skill_group"],
                            placement["title"],
                            activity["variant"],
                            activity["results"]["Student A"]["display"],
                            activity["results"]["Student B"]["display"],
                            placement["aggregate_results"]["Student A"]["display"],
                            placement["aggregate_results"]["Student B"]["display"],
                        ]
                    )

    lines = [
        "# Khan Academy Kids — complete ELA report archive",
        "",
        "Captured from **Class Reports → All Progress → English Language Arts** on 2026-09-08, "
        "using Android app 9.0.1 (versionCode 123).",
        "",
        f"- Grades: {len(grades)}",
        f"- Reported lesson placements: {len(all_placements):,}",
        f"- Assignable activity placements after expanding variants: {activity_count:,}",
        f"- Globally unique title strings: {len(unique_titles):,}",
        "",
        "Results use the app's exact display: a percentage, `Viewed`, an aggregate fraction, or `—` "
        "for a blank cell. A blank is not treated as 0%.",
        "The report does not provide prose descriptions; each lesson's domain and skill-group path records what it covers.",
    ]
    for grade in grades:
        lines.extend(
            [
                "",
                f"## {grade['grade']}",
                "",
                f"{grade['reported_lesson_total']} report lessons; {grade['capture_pages']} captured screens.",
            ]
        )
        current_domain = None
        current_group = None
        for placement in grade["lesson_placements"]:
            if placement["domain"] != current_domain:
                current_domain = placement["domain"]
                current_group = None
                lines.extend(["", f"### {current_domain}", ""])
            if placement["skill_group"] != current_group:
                current_group = placement["skill_group"]
                lines.extend(["", f"#### {current_group}", ""])
            aggregate = placement["aggregate_results"]
            if placement["activities"]:
                lines.append(
                    f"- **{placement['title']}** — aggregate: Student A {result_display(aggregate['Student A'])}; "
                    f"Student B {result_display(aggregate['Student B'])}"
                )
                for activity in placement["activities"]:
                    results = activity["results"]
                    lines.append(
                        f"  - {activity['variant']} — Student A {result_display(results['Student A'])}; "
                        f"Student B {result_display(results['Student B'])}"
                    )
            else:
                lines.append(
                    f"- **{placement['title']}** — Student A {result_display(aggregate['Student A'])}; "
                    f"Student B {result_display(aggregate['Student B'])}"
                )
    args.markdown.write_text("\n".join(lines).rstrip() + "\n")

    print(
        json.dumps(
            {
                "grades": len(grades),
                "lesson_placements": len(all_placements),
                "activity_placements": activity_count,
                "unique_titles": len(unique_titles),
            }
        )
    )


if __name__ == "__main__":
    main()
