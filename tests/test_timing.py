from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.timing import TimingRecorder


class TimingTests(unittest.TestCase):
    def test_snapshot_contains_names_and_aggregates_but_no_arguments(self) -> None:
        timing = TimingRecorder()
        with timing.span("adb.keyevent"):
            pass
        with timing.span("adb.keyevent"):
            pass

        snapshot = timing.snapshot()

        metric = snapshot["steps"][0]
        self.assertEqual(metric["name"], "adb.keyevent")
        self.assertEqual(metric["count"], 2)
        self.assertNotIn("arguments", metric)
