"""Owner-private, run-correlated history for dashboard check-ins."""

from __future__ import annotations

import json
import stat
from contextlib import contextmanager
from datetime import datetime
from fcntl import LOCK_EX, LOCK_SH, LOCK_UN, flock
from pathlib import Path

from .records import write_json_atomic
from .sync_report import build_dashboard_report

HISTORY_VERSION = 1
MAX_CHECKINS = 500


def _slug(student: str) -> str:
    return student.casefold().replace(" ", "-")


def _history_path(root: Path, student: str) -> Path:
    return root / "private/checkin-history" / f"{_slug(student)}.json"


@contextmanager
def _locked(root: Path, *, shared: bool):
    directory = root / "private/checkin-history"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    lock_path = directory / ".lock"
    with lock_path.open("a", encoding="utf-8") as handle:
        lock_path.chmod(0o600)
        flock(handle, LOCK_SH if shared else LOCK_EX)
        try:
            yield
        finally:
            flock(handle, LOCK_UN)


def _read_private_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError("Check-in history requires a regular owner-private file")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Check-in history is invalid")
    return data


def _diagnostic_started_at(root: Path, run_id: object) -> str | None:
    if not isinstance(run_id, str):
        return None
    try:
        manifest = json.loads((root / "private/sync-runs" / run_id / "run.json").read_text())
    except (OSError, ValueError):
        return None
    value = manifest.get("started_at") if isinstance(manifest, dict) else None
    return value if isinstance(value, str) else None


def _completed_at(payload: dict) -> str:
    for key in ("applied_at", "interrupted_at", "verified_at", "generated_at"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize(root: Path, payload: dict) -> dict:
    report = build_dashboard_report(payload)
    student = report.get("student")
    run_id = report.get("run_id")
    if not isinstance(student, str) or not student or not isinstance(run_id, str) or not run_id:
        raise ValueError("A final check-in needs a student and run ID")
    manual = report.get("manual_change")
    action = manual.get("action") if isinstance(manual, dict) else None
    kind = {
        "assign": "manual_assignment",
        "unassign": "manual_unassignment",
    }.get(action, "mastery_sync")
    completed = _completed_at(payload)
    started = (
        payload.get("started_at")
        or _diagnostic_started_at(root, run_id)
        or payload.get("generated_at")
        or completed
    )
    return {
        "version": HISTORY_VERSION,
        "run_id": run_id,
        "kind": kind,
        "student": student,
        "started_at": started,
        "completed_at": completed,
        "report": report,
    }


def _valid_ledger(data: dict, student: str) -> list[dict]:
    if data.get("version") != HISTORY_VERSION or data.get("student") != student:
        raise ValueError("Check-in history belongs to another reader or schema")
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("Check-in history events are invalid")
    valid = []
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("student") != student
            or not isinstance(event.get("run_id"), str)
            or not isinstance(event.get("report"), dict)
        ):
            raise ValueError("Check-in history event is invalid")
        valid.append(event)
    return valid


def record_checkin(root: Path, payload: dict) -> dict:
    """Persist one final event, replacing only an identical run ID on retry."""
    event = _normalize(root, payload)
    student = event["student"]
    path = _history_path(root, student)
    with _locked(root, shared=False):
        existing = _read_private_json(path)
        events = _valid_ledger(existing, student) if existing else []
        events = [item for item in events if item["run_id"] != event["run_id"]]
        events.append(event)
        events.sort(key=lambda item: item["completed_at"], reverse=True)
        write_json_atomic(
            path,
            {"version": HISTORY_VERSION, "student": student, "events": events[:MAX_CHECKINS]},
        )
        path.chmod(0o600)
    return event


def _structured_backfill(root: Path, student: str) -> list[dict]:
    """Import only structured retained results; never infer groups from prose logs."""
    by_run: dict[str, dict] = {}
    for path in (root / "private/sync-runs").glob("*/result.json"):
        try:
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict) or payload.get("student") != student:
                continue
            event = _normalize(root, payload)
            by_run[event["run_id"]] = event
        except (OSError, ValueError, TypeError):
            continue
    return sorted(by_run.values(), key=lambda item: item["completed_at"], reverse=True)


def checkin_history(root: Path, student: str, *, limit: int = 100) -> dict:
    """Return recent final events, safely backfilling retained structured runs once."""
    path = _history_path(root, student)
    with _locked(root, shared=True):
        existing = _read_private_json(path)
    if existing is None:
        events = _structured_backfill(root, student)
        with _locked(root, shared=False):
            # Another process may have created the ledger while backfill ran.
            existing = _read_private_json(path)
            if existing is None:
                write_json_atomic(
                    path,
                    {"version": HISTORY_VERSION, "student": student, "events": events},
                )
                path.chmod(0o600)
                existing = {"version": HISTORY_VERSION, "student": student, "events": events}
    events = _valid_ledger(existing, student)
    return {
        "version": HISTORY_VERSION,
        "student": student,
        "events": events[: max(1, min(limit, 100))],
        "backfill_note": (
            "Earlier reading records remain available, but exact check-in grouping was not "
            "preserved before this history was introduced."
        ),
    }
