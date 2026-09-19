"""Bounded, secret-safe progress output independent of Android operations."""

from __future__ import annotations

import threading
import time
from typing import TextIO


class ProgressReporter:
    """Stream milestones and heartbeats to stderr; never issue device commands."""

    def __init__(self, stream: TextIO, *, interval: float = 10.0) -> None:
        if interval <= 0:
            raise ValueError("Progress interval must be positive")
        self.stream = stream
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._longest = 0.0
        self._output_available = True
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)

    def __enter__(self) -> ProgressReporter:
        self.emit("Starting tablet workflow")
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def emit(self, message: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._longest = max(self._longest, now - self._last)
            self._last = now
            self._write(message)

    def _write(self, message: str) -> None:
        if self._output_available:
            try:
                print(f"[progress] {message}", file=self.stream, flush=True)
            except (OSError, ValueError):
                # A closed diagnostic pipe must never interrupt a saved action.
                self._output_available = False

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {
                "max_silent_seconds": round(max(self._longest, time.monotonic() - self._last), 3)
            }

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.interval):
            with self._lock:
                # A liveness heartbeat is visible reassurance, not workflow progress.
                self._write("Still working; waiting for the current UI operation")
