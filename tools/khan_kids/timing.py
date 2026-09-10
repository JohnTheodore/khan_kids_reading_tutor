"""Low-overhead, secret-safe timing metrics for live automation."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(slots=True)
class _Metric:
    count: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0


class TimingRecorder:
    """Collect named durations without recording arguments or UI content."""

    def __init__(self) -> None:
        self._metrics: defaultdict[str, _Metric] = defaultdict(_Metric)
        self.started_at = time.monotonic()

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            metric = self._metrics[name]
            metric.count += 1
            metric.total_seconds += elapsed
            metric.max_seconds = max(metric.max_seconds, elapsed)

    def snapshot(self) -> dict[str, object]:
        return {
            "wall_seconds": round(time.monotonic() - self.started_at, 3),
            "steps": [
                {
                    "name": name,
                    "count": metric.count,
                    "total_seconds": round(metric.total_seconds, 3),
                    "average_seconds": round(metric.total_seconds / metric.count, 3),
                    "max_seconds": round(metric.max_seconds, 3),
                }
                for name, metric in sorted(
                    self._metrics.items(), key=lambda item: item[1].total_seconds, reverse=True
                )
            ],
        }
