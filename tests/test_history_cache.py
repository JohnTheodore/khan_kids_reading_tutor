from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.catalog import CatalogIndex
from khan_kids.history_cache import HistoryCache
from khan_kids.reports import AssignmentRow, ScoreAttempt, ScoreHistory
from khan_kids.ui import Rect
from khan_kids.workflow import histories_to_attempt_rows


class HistoryCacheTests(unittest.TestCase):
    def test_unchanged_same_day_row_round_trips_exact_history(self) -> None:
        today = date(2026, 9, 10)
        row = _row(score=92)
        history = _history(today, 92)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cache.json"
            cache = HistoryCache.load(path, student="Student A", today=today)
            cache.update((row,), (history,))
            cache.save()

            restored = HistoryCache.load(path, student="Student A", today=today)
            self.assertEqual(restored.lookup(row), history)
            self.assertEqual((restored.hits, restored.misses), (1, 0))

            catalog = CatalogIndex(Path("data/reading-ela-archive.json"))
            preferred = {(history.title, history.variant): "Preschool (Age 4)"}
            live_rows = histories_to_attempt_rows((history,), catalog, preferred_grades=preferred)
            cached_rows = histories_to_attempt_rows(
                (restored.lookup(row),), catalog, preferred_grades=preferred
            )
            self.assertEqual(cached_rows, live_rows)

    def test_score_change_and_new_day_both_force_live_read(self) -> None:
        today = date(2026, 9, 10)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cache.json"
            cache = HistoryCache.load(path, student="Student A", today=today)
            cache.update((_row(score=92),), (_history(today, 92),))
            cache.save()

            self.assertIsNone(cache.lookup(_row(score=100)))
            tomorrow = HistoryCache.load(path, student="Student A", today=date(2026, 9, 11))
            self.assertIsNone(tomorrow.lookup(_row(score=92)))


def _row(*, score: int) -> AssignmentRow:
    return AssignmentRow(
        title="Blend Sounds 2",
        variant="Main",
        assigned_date="Today",
        rect=Rect(0, 100, 500, 200),
        score=score,
        score_rect=Rect(500, 100, 600, 200),
    )


def _history(today: date, score: int) -> ScoreHistory:
    return ScoreHistory(
        student="Student A",
        title="Blend Sounds 2",
        variant="Main",
        curriculum_path="Reading/Preschool (Age 4)",
        assigned_date=today,
        attempts_newest_first=(ScoreAttempt("Today", today, score),),
    )
