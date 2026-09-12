"""Persistent, student-specific temporary lesson-family quarantines."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .records import append_unique_rows_with_records


QUARANTINE_FIELDS = ("student", "title", "start_date", "eligible_date", "reason")
LOW_SCORE_ATTEMPT_THRESHOLD = 4
LOW_SCORE_FLOOR = 70
LOW_SCORE_QUARANTINE_DAYS = 14


@dataclass(frozen=True, slots=True)
class LessonQuarantine:
    student: str
    title: str
    start_date: date
    eligible_date: date
    reason: str

    @property
    def active_through(self) -> date:
        return self.eligible_date - timedelta(days=1)

    def as_dict(self) -> dict[str, str]:
        return {
            "student": self.student,
            "title": self.title,
            "start_date": self.start_date.isoformat(),
            "active_through": self.active_through.isoformat(),
            "eligible_date": self.eligible_date.isoformat(),
            "reason": self.reason,
        }


def read_active_quarantines(
    path: Path, *, student: str, today: date
) -> tuple[LessonQuarantine, ...]:
    """Return active records; a family is eligible again on its eligible date."""
    if not path.exists():
        return ()
    with path.open(newline="") as source:
        rows = tuple(csv.DictReader(source))
    active: list[LessonQuarantine] = []
    for row in rows:
        record = _parse_row(row, path)
        if record.student == student and record.start_date <= today < record.eligible_date:
            active.append(record)
    titles = [record.title for record in active]
    if len(titles) != len(set(titles)):
        raise ValueError(f"duplicate active lesson quarantines in {path}")
    return tuple(active)


def append_low_score_quarantines(
    path: Path,
    *,
    student: str,
    today: date,
    scores: dict[tuple[str, str], tuple[int, ...]],
    new_attempts: Iterable[Mapping[str, object]],
    active_quarantines: tuple[LessonQuarantine, ...] = (),
) -> tuple[LessonQuarantine, ...]:
    """Quarantine newly reassessed families that remain below the score floor."""
    active_titles = {record.title for record in active_quarantines}
    candidates = {
        (str(row.get("lesson_title", "")), str(row.get("activity_variant", "")))
        for row in new_attempts
    }
    rows: list[dict[str, object]] = []
    for title, variant in sorted(candidates):
        history = scores.get((title, variant), ())
        if (
            not title
            or title in active_titles
            or len(history) < LOW_SCORE_ATTEMPT_THRESHOLD
            or history[-1] >= LOW_SCORE_FLOOR
        ):
            continue
        evidence = " → ".join(f"{score}%" for score in history)
        rows.append(
            {
                "student": student,
                "title": title,
                "start_date": today.isoformat(),
                "eligible_date": (today + timedelta(days=LOW_SCORE_QUARANTINE_DAYS)).isoformat(),
                "reason": (
                    f"{LOW_SCORE_QUARANTINE_DAYS}-day instructional quarantine after "
                    f"{len(history)} {variant} attempts; latest score is below "
                    f"{LOW_SCORE_FLOOR}% ({history[-1]}%); scores: {evidence}"
                ),
            }
        )
        active_titles.add(title)
    append_unique_rows_with_records(
        path,
        QUARANTINE_FIELDS,
        rows,
        identity_fields=("student", "title", "start_date"),
    )
    return read_active_quarantines(path, student=student, today=today)


def _parse_row(row: dict[str, str | None], path: Path) -> LessonQuarantine:
    required = ("student", "title", "start_date", "eligible_date", "reason")
    values = {key: row.get(key) for key in required}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise ValueError(f"invalid lesson quarantine row in {path}")
    start = date.fromisoformat(values["start_date"])
    eligible = date.fromisoformat(values["eligible_date"])
    if eligible <= start:
        raise ValueError(f"lesson quarantine eligible_date must follow start_date in {path}")
    return LessonQuarantine(values["student"], values["title"], start, eligible, values["reason"])
