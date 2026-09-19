"""Append secret-safe operational incidents for failed mastery-sync invocations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .diagnostics import sanitize_diagnostic_text
from .records import append_text_atomic


def append_failed_sync_incident(
    path: Path,
    *,
    student: str,
    error: Exception,
    detected_at: datetime | None = None,
    payload: dict[str, object] | None = None,
    run_id: str | None = None,
) -> str:
    """Append one failure record without device identifiers, credentials, or UI contents."""
    timestamp = detected_at or datetime.now().astimezone()
    incident_id = f"KKRT-{timestamp:%Y-%m-%d}-AUTO-{timestamp:%H%M%S-%f}"
    error_text = _safe_error_text(error)
    diagnostic_row = f"| Diagnostic run | `{run_id}` |\n" if run_id else ""
    report = (
        f"\n## {incident_id} — Mastery sync interruption\n\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Date | {timestamp:%Y-%m-%d} |\n"
        "| Severity | SEV-3 — automation interruption; review required before retry |\n"
        "| Status | Open |\n"
        "| Detected by | Automated mastery-sync failure handler |\n"
        f"| Affected student | {student} |\n"
        f"{diagnostic_row}\n"
        "### Observed failure\n\n"
        f"`{type(error).__name__}: {error_text}`\n\n"
        "### Automatic response\n\n"
        "- The invocation stopped with a nonzero exit status.\n"
        "- The normal workflow safety guards remained in force.\n"
        f"{_action_summary(payload)}"
        f"{_recovery_summary(payload)}"
        "- Diagnose the exact device state before retrying.\n"
    )
    append_text_atomic(path, report)
    return incident_id


def _action_summary(payload: dict[str, object] | None) -> str:
    applied = payload.get("applied") if isinstance(payload, dict) else None
    if not isinstance(applied, list) or not applied:
        return "- Completed assignment actions: none recorded.\n"
    safe_actions = []
    for item in applied:
        if not isinstance(item, dict):
            continue
        action = "unchecked" if item.get("action") == "unchecked" else "added"
        safe_actions.append(
            f"{action} {item.get('title', 'unknown')} — {item.get('variant', 'unknown')}"
        )
    return f"- Completed assignment actions: {'; '.join(safe_actions) or 'none recorded'}.\n"


def _recovery_summary(payload: dict[str, object] | None) -> str:
    recovery = payload.get("recovery") if isinstance(payload, dict) else None
    if not isinstance(recovery, dict) or recovery.get("status") != "captured":
        return "- Live queue after interruption: unavailable.\n"
    missing = recovery.get("missing_assignments")
    missing_count = len(missing) if isinstance(missing, list) else 0
    return (
        f"- Live queue after interruption: {recovery.get('live_count', 'unknown')} assignments; "
        f"{missing_count} missing from desired state.\n"
    )


def _safe_error_text(error: Exception) -> str:
    """Keep diagnostics useful without persisting common local secret forms."""
    text = " ".join(str(error).splitlines()).strip() or type(error).__name__
    return sanitize_diagnostic_text(text)
