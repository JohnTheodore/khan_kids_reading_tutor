"""Cooperative, durable cancellation for tablet workflows."""

from __future__ import annotations

from pathlib import Path

from .adb import AutomationError


class WorkflowCancelled(AutomationError):
    """Raised at a safe Android-operation boundary after a stop request."""


class FileCancellationToken:
    """A process-independent stop signal that survives dashboard restarts."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def check(self) -> None:
        if self.path is not None and self.path.exists():
            raise WorkflowCancelled("Sync stopped safely by request")
