"""Append secret-safe operational incidents for failed mastery-sync invocations."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .records import append_text_atomic


def append_failed_sync_incident(
    path: Path, *, student: str, error: Exception, detected_at: datetime | None = None
) -> str:
    """Append one failure record without device identifiers, credentials, or UI contents."""
    timestamp = detected_at or datetime.now().astimezone()
    incident_id = f"KKRT-{timestamp:%Y-%m-%d}-AUTO-{timestamp:%H%M%S-%f}"
    error_text = _safe_error_text(error)
    report = (
        f"\n## {incident_id} — Mastery sync interruption\n\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Date | {timestamp:%Y-%m-%d} |\n"
        "| Severity | SEV-3 — automation interruption; review required before retry |\n"
        "| Status | Open |\n"
        "| Detected by | Automated mastery-sync failure handler |\n"
        f"| Affected student | {student} |\n\n"
        "### Observed failure\n\n"
        f"`{type(error).__name__}: {error_text}`\n\n"
        "### Automatic response\n\n"
        "- The invocation stopped with a nonzero exit status.\n"
        "- The normal workflow safety guards remained in force.\n"
        "- Any completed assignment actions, if present, remain recorded in the student sync log.\n"
        "- Diagnose the exact device state before retrying.\n"
    )
    append_text_atomic(path, report)
    return incident_id


def _safe_error_text(error: Exception) -> str:
    """Keep diagnostics useful without persisting common local secret forms."""
    text = " ".join(str(error).splitlines()).strip() or type(error).__name__
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b", "[redacted device]", text)
    text = re.sub(r"(?<!\d)\d{6}(?!\d)", "[redacted numeric secret]", text)
    text = re.sub(r"/home/[^/\s]+", "/home/[redacted]", text)
    return text.replace("`", "'")
