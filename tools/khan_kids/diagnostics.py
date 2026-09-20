"""Owner-private, bounded diagnostics shared by tablet workflows."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .records import write_json_atomic, write_text_atomic

MAX_DIAGNOSTIC_RUNS = 20
MAX_DIAGNOSTIC_TEXT = 250_000
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def new_run_id() -> str:
    """Return a sortable, path-safe correlation ID."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"sync-{stamp}-{secrets.token_hex(4)}"


def validate_run_id(run_id: str) -> str:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run ID must contain only letters, numbers, dots, underscores, and dashes")
    return run_id


def sanitize_diagnostic_text(value: object) -> str:
    """Redact common local secrets while preserving actionable failure context."""
    text = str(value).replace("`", "'")
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b", "[redacted device]", text)
    text = re.sub(r"(?<!\d)\d{6}(?!\d)", "[redacted numeric secret]", text)
    return re.sub(r"/home/[^/\s]+", "/home/[redacted]", text)


def capture_device_failure(
    directory: Path,
    device: object,
    error: BaseException,
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Write a password-safe device snapshot and bounded Android logs."""
    _write_private_text(directory / "error.txt", f"{type(error).__name__}: {error}\n")
    try:
        mode = device.lock_task_mode()  # type: ignore[attr-defined]
        write_json_atomic(directory / "android-state.json", {"lock_task_mode": mode})
        (directory / "android-state.json").chmod(0o600)
    except Exception:
        pass
    try:
        root = device.hierarchy()  # type: ignore[attr-defined]
        if _has_password_prompt(root):
            if progress:
                progress("Diagnostic screen capture skipped for password dialog")
        else:
            _write_private_text(
                directory / "failure.xml",
                ET.tostring(root, encoding="unicode"),
                sanitize=False,
            )
            screenshot = directory / "failure.png"
            device.screenshot(screenshot)  # type: ignore[attr-defined]
            screenshot.chmod(0o600)
    except Exception as capture_error:
        _write_private_text(
            directory / "capture-error.txt", f"UI capture unavailable: {capture_error}\n"
        )
    try:
        logs = device.command(  # type: ignore[attr-defined]
            "logcat", "-d", "-t", "200", capture=True
        ).decode(errors="replace")
        _write_private_text(directory / "android-logcat.txt", logs)
    except Exception as log_error:
        _write_private_text(
            directory / "logcat-error.txt", f"Android log capture unavailable: {log_error}\n"
        )


def _write_private_text(path: Path, content: str, *, sanitize: bool = True) -> None:
    text = sanitize_diagnostic_text(content) if sanitize else content
    write_text_atomic(path, text[-MAX_DIAGNOSTIC_TEXT:])
    path.chmod(0o600)


def _has_password_prompt(root: ET.Element) -> bool:
    return any(
        "Enter Password" in (node.attrib.get(field) or "")
        for node in root.iter()
        for field in ("text", "content-desc")
    )


class DiagnosticRun:
    """Persist one correlated run without allowing diagnostics to break workflow safety."""

    def __init__(self, root: Path, run_id: str, *, student: str | None = None) -> None:
        self.root = root
        self.run_id = validate_run_id(run_id)
        self.base = root / "private/sync-runs"
        self.path = self.base / self.run_id
        self._captured_device = False
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.base.chmod(0o700)
        self.path.mkdir(mode=0o700, exist_ok=True)
        self.path.chmod(0o700)
        if not (self.path / "run.json").exists():
            self.write_json(
                "run.json",
                {
                    "run_id": self.run_id,
                    "student": student,
                    "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "status": "running",
                },
            )
        self.rotate()

    def write_text(self, name: str, content: str, *, sanitize: bool = True) -> None:
        _write_private_text(self.path / name, content, sanitize=sanitize)

    def append_output(self, content: str) -> None:
        destination = self.path / "output.log"
        with destination.open("a", encoding="utf-8") as output:
            output.write(sanitize_diagnostic_text(content))
        destination.chmod(0o600)
        if destination.stat().st_size > MAX_DIAGNOSTIC_TEXT:
            bounded = destination.read_text(errors="replace")[-MAX_DIAGNOSTIC_TEXT:]
            _write_private_text(destination, bounded, sanitize=False)

    def write_json(self, name: str, payload: object) -> None:
        write_json_atomic(self.path / name, payload)
        (self.path / name).chmod(0o600)

    def record_failure(
        self,
        error: BaseException,
        *,
        payload: dict[str, object] | None = None,
        include_traceback: bool = True,
    ) -> None:
        self.write_text("error.txt", f"{type(error).__name__}: {error}\n")
        if include_traceback:
            self.write_text(
                "traceback.txt",
                "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            )
        if payload is not None:
            self.write_json("result.json", payload)
        self._update_manifest("failed", error=type(error).__name__)

    def record_success(self, payload: dict[str, object] | None = None) -> None:
        if payload is not None:
            self.write_json("result.json", payload)
        self._update_manifest("succeeded")

    def finish(self, status: str, **details: object) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError("diagnostic status must be succeeded or failed")
        self._update_manifest(status, **details)

    def capture_device_failure(
        self,
        device: object,
        error: BaseException,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        """Capture the current UI and bounded Android logs, skipping password screens."""
        if self._captured_device:
            return
        self._captured_device = True
        self.record_failure(error, include_traceback=False)
        capture_device_failure(self.path, device, error, progress=progress)
        if progress:
            progress(f"Private failure evidence saved: private/sync-runs/{self.run_id}")

    def rotate(self) -> None:
        """Retain only the newest bounded set of owner-private run directories."""
        runs = sorted(
            (path for path in self.base.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for stale in runs[MAX_DIAGNOSTIC_RUNS:]:
            shutil.rmtree(stale)

    def _update_manifest(self, status: str, **details: object) -> None:
        destination = self.path / "run.json"
        try:
            manifest = json.loads(destination.read_text())
        except (OSError, ValueError):
            manifest = {"run_id": self.run_id}
        manifest.update(
            status=status,
            finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            **details,
        )
        self.write_json("run.json", manifest)
