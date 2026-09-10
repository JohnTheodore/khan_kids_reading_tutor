"""Structured parsing for Khan Kids assignment reports and score dialogs."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

from .adb import AutomationError
from .constants import LEARNING_SEQUENCE
from .ui import Rect, near, visible_nodes

SCORE_PATTERN = re.compile(r"(\d{1,3})%")
RELATIVE_DATES = {"Today", "Yesterday"}


@dataclass(frozen=True, slots=True)
class ReportLayout:
    lesson_left: int = 235
    variant_left: int = 105
    score_table_left: int = 575
    score_column_width: int = 208
    content_top: int = 385
    screen_bottom: int = 1600
    safe_scroll_x: int = 1200


DEFAULT_REPORT_LAYOUT = ReportLayout()


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    title: str
    variant: str
    assigned_date: str
    rect: Rect
    score: int | None
    score_rect: Rect | None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.title, self.variant, self.assigned_date)


@dataclass(frozen=True, slots=True)
class ScoreAttempt:
    display_date: str
    attempt_date: date
    score: int


@dataclass(frozen=True, slots=True)
class ScoreHistory:
    student: str
    title: str
    variant: str
    curriculum_path: str
    assigned_date: date
    attempts_newest_first: tuple[ScoreAttempt, ...]


@dataclass(frozen=True, slots=True)
class AssignmentSnapshot:
    rows: tuple[AssignmentRow, ...]
    histories: tuple[ScoreHistory, ...]


def is_assignment_report(root: ET.Element) -> bool:
    return any(item.text == "Class Report: Assignments" for item in visible_nodes(root))


def parse_assignment_rows(
    root: ET.Element,
    student: str,
    *,
    roster: tuple[str, ...],
    layout: ReportLayout = DEFAULT_REPORT_LAYOUT,
) -> list[AssignmentRow]:
    if not is_assignment_report(root):
        raise AutomationError("Expected Class Report: Assignments")
    try:
        student_index = roster.index(student)
    except ValueError as error:
        raise AutomationError(f"Student {student!r} is absent from roster {roster!r}") from error
    nodes = visible_nodes(root)
    titles = [
        item
        for item in nodes
        if near(item.rect.left, layout.lesson_left)
        and layout.content_top <= item.rect.top < layout.screen_bottom
        and not _looks_like_date(item.text)
    ]
    titles.sort(key=lambda item: item.rect.top)
    rows: list[AssignmentRow] = []
    score_left = layout.score_table_left + student_index * layout.score_column_width
    score_right = score_left + layout.score_column_width
    for index, title in enumerate(titles):
        row_top = title.rect.top
        row_bottom = (
            titles[index + 1].rect.top
            if index + 1 < len(titles)
            else min(layout.screen_bottom, row_top + 96)
        )
        variants = [
            item.text
            for item in nodes
            if near(item.rect.left, layout.variant_left)
            and row_top <= item.rect.center[1] < row_bottom
            and item.text in LEARNING_SEQUENCE
        ]
        dates = [
            item.text
            for item in nodes
            if near(item.rect.left, layout.lesson_left)
            and row_top < item.rect.top < row_bottom
            and _looks_like_date(item.text)
        ]
        score_nodes = [
            item
            for item in nodes
            if score_left <= item.rect.left < score_right
            and row_top <= item.rect.center[1] < row_bottom
            and SCORE_PATTERN.fullmatch(item.text)
        ]
        if len(variants) != 1 or len(dates) != 1 or len(score_nodes) > 1:
            continue
        score_node = score_nodes[0] if score_nodes else None
        rows.append(
            AssignmentRow(
                title=title.text,
                variant=variants[0],
                assigned_date=dates[0],
                rect=Rect(65, row_top, 774, row_bottom),
                score=int(score_node.text[:-1]) if score_node else None,
                score_rect=score_node.rect if score_node else None,
            )
        )
    return rows


def parse_score_history(
    root: ET.Element, student: str, *, today: date, assigned_display_date: str
) -> ScoreHistory:
    nodes = visible_nodes(root)
    if not any(item.text == f"{student}'s Lesson Scores" for item in nodes):
        raise AutomationError(f"Expected {student!r} score-history dialog")
    activity = next(
        (
            item.text
            for item in nodes
            if any(item.text.endswith(f": {variant}") for variant in LEARNING_SEQUENCE)
            and 900 <= item.rect.left <= 1200
        ),
        None,
    )
    if activity is None:
        raise AutomationError("Score-history dialog has no activity title/variant")
    title, variant = activity.rsplit(": ", 1)
    curriculum = next(
        (
            item.text.replace("\n", " ")
            for item in nodes
            if item.rect.left >= 900
            and (": ELA:" in item.text or ": Reading Foundational Skills:" in item.text)
        ),
        "",
    )
    date_nodes = [
        item
        for item in nodes
        if item.rect.left >= 1000 and item.rect.top >= 760 and _looks_like_history_date(item.text)
    ]
    score_nodes = [
        item
        for item in nodes
        if item.rect.left >= 1300 and item.rect.top >= 760 and SCORE_PATTERN.fullmatch(item.text)
    ]
    attempts: list[ScoreAttempt] = []
    for displayed in sorted(date_nodes, key=lambda item: item.rect.top):
        score = min(
            score_nodes,
            key=lambda item: abs(item.rect.center[1] - displayed.rect.center[1]),
            default=None,
        )
        if score is None or abs(score.rect.center[1] - displayed.rect.center[1]) > 20:
            raise AutomationError(f"No score aligned with history date {displayed.text!r}")
        attempts.append(
            ScoreAttempt(
                display_date=displayed.text,
                attempt_date=resolve_history_date(displayed.text, today=today),
                score=int(score.text[:-1]),
            )
        )
    if not attempts:
        raise AutomationError("Score-history dialog contains no dated attempts")
    return ScoreHistory(
        student=student,
        title=title,
        variant=variant,
        curriculum_path=curriculum,
        assigned_date=resolve_history_date(assigned_display_date, today=today),
        attempts_newest_first=tuple(attempts),
    )


def resolve_history_date(raw: str, *, today: date) -> date:
    if raw == "Today":
        return today
    if raw == "Yesterday":
        return today - timedelta(days=1)
    normalized = " ".join(raw.replace(".", "").split())
    match = re.fullmatch(r"[A-Za-z]{3}, ([A-Za-z]{3}) (\d{1,2})", normalized)
    if not match:
        raise AutomationError(f"Unsupported Khan Kids history date: {raw!r}")
    month = {
        name: number
        for number, name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
            start=1,
        )
    }[match.group(1)]
    result = date(today.year, month, int(match.group(2)))
    return result.replace(year=result.year - 1) if result > today else result


def _looks_like_date(text: str) -> bool:
    if text in RELATIVE_DATES:
        return True
    normalized = " ".join(text.replace(".", "").split())
    return bool(re.fullmatch(r"[A-Za-z]{3}, [A-Za-z]{3} \d{1,2}", normalized))


def _looks_like_history_date(text: str) -> bool:
    return _looks_like_date(text)
