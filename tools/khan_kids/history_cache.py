"""Same-day cache for score dialogs whose visible assignment row is unchanged."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .records import write_json_atomic
from .reports import AssignmentRow, ScoreAttempt, ScoreHistory

CACHE_VERSION = 1


@dataclass(slots=True)
class HistoryCache:
    path: Path
    student: str
    today: date
    entries: dict[str, dict[str, object]]
    hits: int = 0
    misses: int = 0

    @classmethod
    def load(cls, path: Path, *, student: str, today: date) -> HistoryCache:
        if not path.exists():
            return cls(path, student, today, {})
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return cls(path, student, today, {})
        if (
            not isinstance(payload, dict)
            or payload.get("version") != CACHE_VERSION
            or payload.get("student") != student
            or not isinstance(payload.get("entries"), dict)
        ):
            return cls(path, student, today, {})
        return cls(path, student, today, payload["entries"])

    def lookup(self, row: AssignmentRow) -> ScoreHistory | None:
        entry = self.entries.get(_row_key(row))
        if not _entry_matches(entry, row, self.today):
            self.misses += 1
            return None
        try:
            history = _history_from_dict(entry["history"])
        except (KeyError, TypeError, ValueError):
            self.misses += 1
            return None
        if history.student != self.student:
            self.misses += 1
            return None
        self.hits += 1
        return history

    def update(self, rows: tuple[AssignmentRow, ...], histories: tuple[ScoreHistory, ...]) -> None:
        by_key = {(history.title, history.variant): history for history in histories}
        for row in rows:
            history = by_key.get((row.title, row.variant))
            if history is None or row.score is None:
                continue
            self.entries[_row_key(row)] = {
                "observed_date": self.today.isoformat(),
                "visible_score": row.score,
                "history": _history_as_dict(history),
            }

    def save(self) -> None:
        write_json_atomic(
            self.path,
            {
                "version": CACHE_VERSION,
                "student": self.student,
                "entries": self.entries,
            },
        )


def _row_key(row: AssignmentRow) -> str:
    return "\u241f".join((row.title, row.variant, row.assigned_date))


def _entry_matches(entry: object, row: AssignmentRow, today: date) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("observed_date") == today.isoformat()
        and entry.get("visible_score") == row.score
        and isinstance(entry.get("history"), dict)
    )


def _history_as_dict(history: ScoreHistory) -> dict[str, object]:
    return {
        "student": history.student,
        "title": history.title,
        "variant": history.variant,
        "curriculum_path": history.curriculum_path,
        "assigned_date": history.assigned_date.isoformat(),
        "attempts": [
            {
                "display_date": attempt.display_date,
                "attempt_date": attempt.attempt_date.isoformat(),
                "score": attempt.score,
            }
            for attempt in history.attempts_newest_first
        ],
    }


def _history_from_dict(payload: object) -> ScoreHistory:
    if not isinstance(payload, dict):
        raise TypeError("history must be an object")
    attempts = payload["attempts"]
    if not isinstance(attempts, list):
        raise TypeError("attempts must be a list")
    return ScoreHistory(
        student=str(payload["student"]),
        title=str(payload["title"]),
        variant=str(payload["variant"]),
        curriculum_path=str(payload["curriculum_path"]),
        assigned_date=date.fromisoformat(str(payload["assigned_date"])),
        attempts_newest_first=tuple(
            ScoreAttempt(
                display_date=str(attempt["display_date"]),
                attempt_date=date.fromisoformat(str(attempt["attempt_date"])),
                score=int(attempt["score"]),
            )
            for attempt in attempts
            if isinstance(attempt, dict)
        ),
    )
