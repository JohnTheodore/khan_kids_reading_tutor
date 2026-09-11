"""Persistent, student-specific temporary lesson-family quarantines."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


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
