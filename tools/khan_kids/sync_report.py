"""Human-readable, append-only reports for reading workflow runs."""

from __future__ import annotations

from pathlib import Path

from .records import append_text_atomic


def append_sync_report(path: Path, payload: dict[str, object]) -> None:
    """Append one complete review or apply report."""
    _append_report(path, render_sync_report(payload))


def append_performance_report(
    path: Path,
    timing: dict[str, object],
    *,
    status: str,
    backend: str,
    cache_hits: int,
    cache_misses: int,
) -> None:
    """Append secret-safe operational timings for one invocation."""
    lines = [
        "### Performance",
        "",
        f"- Outcome: {status}",
        f"- UI backend: {backend}",
        f"- Wall time: {timing.get('wall_seconds', 0)} seconds",
        f"- Score-history cache: {cache_hits} hits, {cache_misses} misses",
        "",
        "| Step | Calls | Total (s) | Average (s) | Max (s) |",
        "|---|---:|---:|---:|---:|",
    ]
    steps = timing.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            lines.append(
                f"| {step.get('name', '')} | {step.get('count', 0)} | "
                f"{step.get('total_seconds', 0)} | {step.get('average_seconds', 0)} | "
                f"{step.get('max_seconds', 0)} |"
            )
    _append_report(path, "\n".join(lines).rstrip() + "\n")


def render_sync_report(payload: dict[str, object]) -> str:
    status = str(payload["status"])
    timestamp = str(
        payload.get("applied_at") or payload.get("interrupted_at") or payload["generated_at"]
    )
    actions = _reported_actions(payload)
    removals = [action for action in actions if action.get("kind") == "remove"]
    additions = [action for action in actions if action.get("kind") == "add"]
    mastered = [
        action for action in removals if str(action.get("reason", "")).startswith("mastered:")
    ]
    promoted = [action for action in mastered if "; promote to " in str(action.get("reason", ""))]
    verb = {
        "applied": "Applied",
        "interrupted": "Applied before interruption",
        "no_op": "Verified",
    }.get(status, "Planned")
    desired = _object_list(payload.get("desired_assignments"))
    tracks = _object_list(payload.get("track_states"))

    lines = [
        f"## {timestamp} — {status}",
        "",
        f"- Student: {payload['student']}",
        f"- Path: {payload.get('path_id', 'unknown')}",
        f"- New attempt records: {payload.get('new_attempt_records', 0)}",
        f"- Desired queue size: {len(desired)}",
        f"- Assignment changes: {len(actions)}",
        "",
        "### Mastered",
        "",
        *_action_lines(mastered, empty="None this run."),
        "",
        f"### {verb} unchecks",
        "",
        *_action_lines(removals, empty="None."),
        "",
        f"### {verb} promotions",
        "",
        *_promotion_lines(promoted),
        "",
        f"### {verb} additions",
        "",
        *_action_lines(additions, empty="None."),
        "",
        "### Desired queue",
        "",
        *([f"- {_lesson(item)}" for item in desired] if desired else ["No lessons selected."]),
        "",
        "### Mastery holds in the desired queue",
        "",
        *_hold_lines(tracks, desired),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _append_report(path: Path, report: str) -> None:
    separator = "\n" if path.exists() and path.read_text() else ""
    append_text_atomic(path, separator + report)


def _reported_actions(payload: dict[str, object]) -> list[dict[str, object]]:
    actions = _object_list(payload.get("actions"))
    if payload.get("status") != "interrupted":
        return actions
    applied = _object_list(payload.get("applied"))
    applied_keys = {
        (
            "remove" if item.get("action") == "unchecked" else "add",
            item.get("title"),
            item.get("variant"),
        )
        for item in applied
        if item.get("action") in {"checked", "unchecked"}
    }
    return [
        action
        for action in actions
        if (action.get("kind"), action.get("title"), action.get("variant")) in applied_keys
    ]


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _lesson(item: dict[str, object]) -> str:
    return f"{item.get('title', 'unknown')} — {item.get('variant', 'unknown')}"


def _action_lines(actions: list[dict[str, object]], *, empty: str) -> list[str]:
    if not actions:
        return [empty]
    return [f"- {_lesson(action)}: {action.get('reason', '')}" for action in actions]


def _promotion_lines(actions: list[dict[str, object]]) -> list[str]:
    if not actions:
        return ["None."]
    lines = []
    for action in actions:
        reason = str(action.get("reason", ""))
        destination = reason.split("; promote to ", 1)[1]
        lines.append(f"- {_lesson(action)} → {destination}")
    return lines


def _hold_lines(tracks: list[dict[str, object]], desired: list[dict[str, object]]) -> list[str]:
    desired_keys = {(item.get("title"), item.get("variant")) for item in desired}
    holds = []
    for track in tracks:
        activity = track.get("next")
        if not isinstance(activity, dict):
            continue
        if (activity.get("title"), activity.get("variant")) not in desired_keys:
            continue
        scores = track.get("scores")
        score_text = ", ".join(f"{score}%" for score in scores) if isinstance(scores, list) else ""
        evidence = score_text or "not attempted"
        holds.append(f"- {_lesson(activity)}: {track.get('status')} ({evidence})")
    return holds or ["None."]
