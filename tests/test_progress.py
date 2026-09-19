from __future__ import annotations

import io
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.progress import ProgressReporter
from khan_kids.timing import TimingRecorder


class ProgressTests(unittest.TestCase):
    def test_closed_diagnostic_stream_does_not_interrupt_workflow(self) -> None:
        stream = io.StringIO()
        stream.close()
        with ProgressReporter(stream) as reporter:
            reporter.emit("Verified action")
        self.assertFalse(reporter._output_available)

    def test_nonpositive_heartbeat_interval_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            ProgressReporter(io.StringIO(), interval=0)

    def test_milestones_flush_and_heartbeat_thread_stops_on_failure(self) -> None:
        stream = io.StringIO()
        reporter = ProgressReporter(stream, interval=0.01)
        heartbeat = threading.Event()
        original_write = reporter._write

        def write(message: str) -> None:
            original_write(message)
            if message.startswith("Still working"):
                heartbeat.set()

        reporter._write = write
        with self.assertRaisesRegex(RuntimeError, "test"), reporter:
            timing = TimingRecorder(reporter.emit)
            with timing.span("phase.review_assignments"):
                self.assertTrue(heartbeat.wait(1))
            with timing.span("adb.keyevent"):
                pass
            raise RuntimeError("test")
        self.assertFalse(reporter._thread.is_alive())
        text = stream.getvalue()
        self.assertIn("Starting phase.review_assignments", text)
        self.assertIn("Still working", text)
        self.assertNotIn("adb.keyevent", text)
        self.assertIn("max_silent_seconds", reporter.snapshot())
        # Snapshot values are rounded to milliseconds; an on-time heartbeat can
        # therefore equal the configured interval on faster CI runners.
        self.assertGreaterEqual(reporter.snapshot()["max_silent_seconds"], reporter.interval)
