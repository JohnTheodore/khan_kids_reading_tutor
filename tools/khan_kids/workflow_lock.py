"""Process-level exclusion for tablet workflows and their local records."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .adb import AutomationError


@contextmanager
def exclusive_workflow_lock(path: Path) -> Iterator[None]:
    """Fail fast when another process owns the shared tablet workflow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AutomationError("Another Khan Kids workflow is already running") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
