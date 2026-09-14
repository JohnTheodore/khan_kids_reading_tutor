"""Shared parsing and naming for read-only Khan Kids All Progress archives."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .adb import AutomationError
from .ui import Rect, UiText, node_rect, visible_nodes

REPORT_SUBJECTS = {
    "ela": "English Language Arts",
    "math": "Math",
    "logic": "Logic+",
    "books": "Books",
    "videos": "Videos",
}


@dataclass(frozen=True, slots=True)
class ReportColumn:
    student: str
    left: int
    right: int


def report_columns(roster: tuple[str, ...]) -> tuple[ReportColumn, ...]:
    """Return score-cell columns in the roster order used by Khan's report."""
    if not roster:
        raise ValueError("report roster cannot be empty")
    return tuple(
        ReportColumn(student, 570 + index * 210, 780 + index * 210)
        for index, student in enumerate(roster)
    )


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


def report_outline_rows(root: ET.Element) -> list[dict[str, object]]:
    rows = []
    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()
        rect = node_rect(node)
        if (
            text
            and rect is not None
            and 385 <= rect.top < 1600
            and rect.left >= 90
            and rect.right <= 570
        ):
            rows.append({"text": text, "bounds": rect.as_list(), "x": rect.left, "y": rect.top})
    unique = {(row["text"], tuple(row["bounds"])): row for row in rows}
    return sorted(unique.values(), key=lambda row: (row["y"], row["x"]))


def report_rows(root: ET.Element, roster: tuple[str, ...]) -> list[dict[str, object]]:
    nodes = [item for item in visible_nodes(root) if item.text]
    rows = []
    for left_node in nodes:
        box = left_node.rect
        if not (385 <= box.top < 1600 and box.left >= 90 and box.right <= 570):
            continue
        results: dict[str, dict[str, object]] = {}
        result_bounds: dict[str, list[int] | None] = {}
        for column in report_columns(roster):
            matches = [
                item
                for item in nodes
                if column.left <= item.rect.left < column.right
                and box.top <= item.rect.center[1] <= box.bottom
            ]
            displays = list(dict.fromkeys(item.text for item in matches))
            raw = " | ".join(displays) if displays else None
            results[column.student] = score_value(raw)
            result_bounds[column.student] = matches[0].rect.as_list() if len(matches) == 1 else None
        rows.append(
            {
                "text": left_node.text,
                "x": box.left,
                "bounds": box.as_list(),
                "results": results,
                "result_bounds": result_bounds,
            }
        )
    unique = {(row["text"], tuple(row["bounds"])): row for row in rows}
    return sorted(unique.values(), key=lambda row: (row["bounds"][1], row["x"]))


def report_filter_value(root: ET.Element) -> str:
    nodes = visible_nodes(root)
    labels = [item for item in nodes if item.text == "Subject:"]
    if len(labels) != 1:
        raise AutomationError(f"Expected one Subject filter label, found {len(labels)}")
    label = labels[0]
    values = [
        item
        for item in nodes
        if item.rect.left > label.rect.right
        and item.rect.right < 800
        and abs(item.rect.center[1] - label.rect.center[1]) < 30
    ]
    if len(values) != 1:
        raise AutomationError(f"Expected one Subject filter value, found {len(values)}")
    return values[0].text


_DISPLAY_DATE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.,\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.\s+(\d{1,2})$"
)


def infer_dated_display(display: str, *, captured_on: date) -> tuple[str | None, str]:
    """Infer the most recent matching calendar date while retaining uncertainty."""
    match = _DISPLAY_DATE.fullmatch(display)
    if not match:
        return None, "unparsed"
    weekday, month, day = match.groups()
    for year in range(captured_on.year, captured_on.year - 12, -1):
        try:
            candidate = datetime.strptime(f"{year} {month} {day}", "%Y %b %d").date()
        except ValueError:
            continue
        if candidate <= captured_on and candidate.strftime("%a") == weekday:
            return candidate.isoformat(), "most_recent_matching_weekday; year_not_displayed_by_app"
    return None, "year_not_displayed_by_app"


def parse_progress_history(
    root: ET.Element, student: str, *, captured_on: date
) -> dict[str, object]:
    """Parse a read-only All Progress Skills Scores or Lesson Scores dialog."""
    nodes = visible_nodes(root)
    headings = [
        item
        for item in nodes
        if item.text in {f"{student}'s Skills Scores", f"{student}'s Lesson Scores"}
    ]
    if len(headings) != 1:
        raise AutomationError(f"Expected one progress-history dialog for {student!r}")
    heading = headings[0]
    content = [
        item
        for item in nodes
        if 800 <= item.rect.left <= 1760 and item.rect.top > heading.rect.bottom
    ]
    paths = [
        item
        for item in content
        if re.match(r"^[A-Z]\d+:", item.text) and item.rect.top < heading.rect.bottom + 220
    ]
    first_date_top = min(
        (item.rect.top for item in content if _DISPLAY_DATE.fullmatch(item.text)),
        default=heading.rect.bottom + 300,
    )
    titles = [
        item
        for item in content
        if item.rect.top < first_date_top and (not paths or item.rect.bottom <= paths[0].rect.top)
    ]
    if len(titles) != 1 or len(paths) > 1:
        raise AutomationError("Progress-history dialog title/path geometry was not recognized")

    dated = sorted((item for item in content if _DISPLAY_DATE.fullmatch(item.text)), key=_top)
    markers = sorted(
        (
            item
            for item in content
            if item.rect.left >= 1200
            and item.text in {"Basic", "Main", "Practice 1", "Practice 2", "Viewed"}
        ),
        key=_top,
    )
    scores = sorted(
        (item for item in content if item.rect.left >= 1300 and re.fullmatch(r"\d+%", item.text)),
        key=_top,
    )
    attempts = []
    current_display_date: str | None = None
    date_index = 0
    for event in markers or scores:
        while (
            date_index < len(dated) and dated[date_index].rect.center[1] <= event.rect.center[1] + 8
        ):
            current_display_date = dated[date_index].text
            date_index += 1
        score = next(
            (item for item in scores if abs(item.rect.center[1] - event.rect.center[1]) <= 8),
            None,
        )
        normalized, method = (
            infer_dated_display(current_display_date, captured_on=captured_on)
            if current_display_date
            else (None, "date_not_displayed")
        )
        attempts.append(
            {
                "display_date": current_display_date,
                "normalized_date": normalized,
                "date_resolution": method,
                "variant": (
                    event.text if event in markers and event.text != "Viewed" else "Direct"
                ),
                "result": "Viewed" if event.text == "Viewed" else score.text if score else None,
                "score_percent": int(score.text.rstrip("%")) if score else None,
            }
        )
    return {
        "student": student,
        "lesson_title": titles[0].text,
        "curriculum_path": paths[0].text.replace("\n", " ") if paths else None,
        "attempts": attempts,
    }


parse_skills_history = parse_progress_history


def _top(item: UiText) -> int:
    return item.rect.top


def rect_from_list(values: list[int]) -> Rect:
    return Rect(*values)


def progress_history_close_rect(root: ET.Element, student: str) -> Rect:
    """Resolve the image-backed close button only on a verified history dialog."""
    headings = {f"{student}'s Skills Scores", f"{student}'s Lesson Scores"}
    if not headings & {item.text for item in visible_nodes(root)}:
        raise AutomationError("Cannot resolve history close control outside a history dialog")
    return _unique_close_rect(
        root,
        description="progress-history",
        position=lambda rect: rect.left > 1500 and 50 < rect.top < 1000,
    )


def assignment_dialog_close_rect(root: ET.Element) -> Rect:
    """Resolve an assignment dialog's image-backed close button without saving."""
    texts = {item.text for item in visible_nodes(root)}
    if "Save" not in texts or not any(text.startswith("Assign\n") for text in texts):
        raise AutomationError("Cannot resolve assignment close control outside its dialog")
    return _unique_close_rect(
        root,
        description="assignment-dialog",
        position=lambda rect: rect.left > 2000 and rect.top < 250,
    )


def _unique_close_rect(
    root: ET.Element, *, description: str, position: Callable[[Rect], bool]
) -> Rect:
    """Find one image-backed square close control within validated dialog bounds."""
    candidates = []
    for node in root.iter("node"):
        rect = node_rect(node)
        if (
            node.attrib.get("class") == "android.view.ViewGroup"
            and rect is not None
            and 90 <= rect.width <= 160
            and 90 <= rect.height <= 160
            and position(rect)
        ):
            candidates.append(rect)
    if len(candidates) != 1:
        raise AutomationError(f"Expected one {description} close control, found {len(candidates)}")
    return candidates[0]


def parse_page(path: Path, roster: tuple[str, ...]) -> list[dict[str, object]]:
    return report_rows(ET.parse(path).getroot(), roster)
