"""Shared transformations between live report histories and durable records."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .catalog import CatalogIndex
from .reports import ScoreHistory


def histories_to_attempt_rows(
    histories: Iterable[ScoreHistory], catalog: CatalogIndex
) -> list[dict[str, object]]:
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows: list[dict[str, object]] = []
    for history in histories:
        entry = catalog.find(history.title, history.variant, history.curriculum_path)
        for attempt in reversed(history.attempts_newest_first):
            rows.append(
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
    return rows


def overlay_live_scores(
    stored: dict[tuple[str, str], tuple[int, ...]], histories: Iterable[ScoreHistory]
) -> dict[tuple[str, str], tuple[int, ...]]:
    """Use complete live histories for active lessons and stored scores for past lessons."""
    result = dict(stored)
    for history in histories:
        result[(history.title, history.variant)] = tuple(
            attempt.score for attempt in reversed(history.attempts_newest_first)
        )
    return result
