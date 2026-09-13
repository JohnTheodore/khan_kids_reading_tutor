"""Human-readable, append-only reports for reading workflow runs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TextIO

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
    actions, removals, additions, mastered = _action_groups(payload)
    promoted = [action for action in mastered if "; promote to " in str(action.get("reason", ""))]
    verb = {
        "applied": "Applied",
        "interrupted": "Applied before interruption",
        "no_op": "Verified",
    }.get(status, "Planned")
    desired = _object_list(payload.get("desired_assignments"))
    stretches = _object_list(payload.get("stretch_assignments"))
    tracks = _object_list(payload.get("track_states"))
    quarantines = _object_list(payload.get("active_quarantines"))
    evidence = _evidence_index(payload)

    lines = [
        f"## {timestamp} — {status}",
        "",
        f"- Student: {payload['student']}",
        f"- Path: {payload.get('path_id', 'unknown')}",
        f"- New attempt records: {payload.get('new_attempt_records', 0)}",
        f"- Desired queue size: {len(desired)}",
        f"- Assignment changes: {len(actions)}",
        "",
        "### New scores",
        "",
        *_new_score_lines(payload),
        "",
        "### Score controls read",
        "",
        *_score_control_lines(payload),
        "",
        "### Mastered",
        "",
        *_action_lines(mastered, evidence=evidence, empty="None this run."),
        "",
        f"### {verb} unchecks",
        "",
        *_action_lines(removals, evidence=evidence, empty="None."),
        "",
        f"### {verb} promotions",
        "",
        *_promotion_lines(promoted),
        "",
        f"### {verb} additions",
        "",
        *_addition_lines(additions, mastered, empty="None."),
        "",
        "### Active quarantines",
        "",
        *_quarantine_lines(quarantines),
        "",
        "### Desired queue",
        "",
        *([f"- {_lesson(item)}" for item in desired] if desired else ["No lessons selected."]),
        "",
        "### Stretch slots",
        "",
        *([f"- {_lesson(item)}" for item in stretches] if stretches else ["None."]),
        "",
        "### Mastery holds in the desired queue",
        "",
        *_hold_lines(tracks, desired),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def terminal_color_enabled(mode: str, stream: TextIO) -> bool:
    """Resolve standard auto/always/never terminal color behavior."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return "NO_COLOR" not in os.environ and stream.isatty() and os.environ.get("TERM") != "dumb"


def render_terminal_summary(payload: dict[str, object], *, color: bool = False) -> str:
    """Render the mandatory human-readable result printed after every successful run."""
    status = str(payload.get("status", "unknown"))
    actions, removals, additions, mastered = _action_groups(payload)
    desired = _object_list(payload.get("desired_assignments"))
    observed = _object_list(payload.get("observed_assignments"))
    quarantines = _object_list(payload.get("active_quarantines"))
    evidence = _evidence_index(payload)
    performance = payload.get("performance")
    duration = performance.get("wall_seconds") if isinstance(performance, dict) else None

    new_attempt_count = int(payload.get("new_attempt_records", 0))
    lines = [
        _paint(
            f"Khan Mastery Sync — {payload.get('student', 'Unknown student')}",
            "bold",
            color,
        ),
        "=" * 60,
        _paint(f"Outcome: {_outcome(status)}", _outcome_style(status), color),
        f"Queue: {len(observed)} before → {len(desired)} desired",
        f"New attempt records: {new_attempt_count}",
    ]
    if duration is not None:
        lines.append(f"Duration: {duration} seconds")

    lines.extend(
        [
            "",
            _paint("CHANGES SINCE LAST SYNC", "bold", color),
            *_change_summary_lines(new_attempt_count, mastered, removals, additions, color=color),
            "",
            _paint("NEW SCORES", "yellow", color),
            *_terminal_new_scores(payload, color=color),
            "",
            _paint(_score_control_heading(payload), "bold", color),
            *_terminal_score_controls(payload, color=color),
            "",
            _paint("MASTERY FOUND", "green", color),
            *_terminal_actions(mastered, evidence, style="green", color=color),
        ]
    )

    removal_heading, addition_heading = _terminal_action_headings(status)
    lines.extend(
        [
            "",
            _paint(removal_heading, "magenta", color),
            *_terminal_actions(removals, evidence, style="magenta", color=color),
            "",
            _paint(addition_heading, "blue", color),
            *_terminal_additions(additions, mastered, color=color),
            "",
            _paint("ACTIVE QUARANTINES", "magenta", color),
            *_terminal_quarantines(quarantines, color=color),
            "",
            _paint(f"ASSIGNED NOW ({len(desired)})", "bold", color),
        ]
    )
    stretches = {
        (item.get("title"), item.get("variant"))
        for item in _object_list(payload.get("stretch_assignments"))
    }
    addition_keys = {(item.get("title"), item.get("variant")) for item in additions}
    new_attempt_keys = {
        (item.get("title"), item.get("variant"))
        for item in _object_list(payload.get("new_attempts"))
    }
    for item in desired:
        key = (item.get("title"), item.get("variant"))
        record = evidence.get(key)
        role = "stretch" if key in stretches else "core"
        if record is None:
            lines.append(
                _paint(
                    f"  · {_lesson(item)} [{role}] — score evidence unavailable",
                    "dim",
                    color,
                )
            )
            continue
        scores = _scores_text(record.get("scores"))
        state = _queue_state(record)
        line = f"  · {_lesson(item)} [{role}] — {state}; scores: {scores}"
        if key in addition_keys:
            lines.append(_paint(line, "blue", color))
        elif key in new_attempt_keys:
            lines.append(_paint(line, "yellow", color))
        else:
            lines.append(_paint(line, "dim", color))
    if not desired:
        lines.append("  None.")
    return "\n".join(lines)


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


def _action_groups(
    payload: dict[str, object],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    actions = _reported_actions(payload)
    removals = [action for action in actions if action.get("kind") == "remove"]
    additions = [action for action in actions if action.get("kind") == "add"]
    mastered = [
        action for action in removals if str(action.get("reason", "")).startswith("mastered:")
    ]
    return actions, removals, additions, mastered


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _lesson(item: dict[str, object]) -> str:
    return f"{item.get('title', 'unknown')} — {item.get('variant', 'unknown')}"


def _action_lines(
    actions: list[dict[str, object]],
    *,
    evidence: dict[tuple[object, object], dict[str, object]],
    empty: str,
) -> list[str]:
    if not actions:
        return [empty]
    lines = []
    for action in actions:
        record = evidence.get((action.get("title"), action.get("variant")))
        lines.append(f"- {_lesson(action)}")
        lines.append(f"  - Scores: {_scores_text(record.get('scores') if record else None)}")
        lines.append(f"  - Why: {_friendly_reason(action.get('reason'))}")
    return lines


def _addition_lines(
    actions: list[dict[str, object]],
    mastered: list[dict[str, object]],
    *,
    empty: str,
) -> list[str]:
    if not actions:
        return [empty]
    return [f"- {_lesson(action)}: {_addition_reason(action, mastered)}" for action in actions]


def _quarantine_lines(quarantines: list[dict[str, object]]) -> list[str]:
    if not quarantines:
        return ["None."]
    return [
        f"- {item.get('title', 'unknown')}: excluded through "
        f"{item.get('active_through', 'unknown')}; eligible again "
        f"{item.get('eligible_date', 'unknown')}. Why: {item.get('reason', 'not recorded')}"
        for item in quarantines
    ]


def _terminal_quarantines(quarantines: list[dict[str, object]], *, color: bool) -> list[str]:
    if not quarantines:
        return ["  None."]
    lines: list[str] = []
    for item in quarantines:
        lines.extend(
            _paint(line, "magenta", color)
            for line in (
                f"  ⏸ {item.get('title', 'unknown')}",
                f"    Excluded through {item.get('active_through', 'unknown')}; "
                f"eligible again {item.get('eligible_date', 'unknown')}",
                f"    Why: {item.get('reason', 'not recorded')}",
            )
        )
    return lines


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


def _new_score_lines(payload: dict[str, object]) -> list[str]:
    attempts = _object_list(payload.get("new_attempts"))
    if not attempts:
        count = int(payload.get("new_attempt_records", 0))
        return ["None."] if count == 0 else [f"{count} new record(s); details unavailable."]
    evidence = _evidence_index(payload)
    lines = []
    for attempt in attempts:
        record = evidence.get((attempt.get("title"), attempt.get("variant")))
        lines.append(
            f"- {_lesson(attempt)}: {attempt.get('score')}% on "
            f"{attempt.get('attempt_date')} (history: "
            f"{_scores_text(record.get('scores') if record else None)})"
        )
    return lines


def _terminal_new_scores(payload: dict[str, object], *, color: bool) -> list[str]:
    if not _object_list(payload.get("new_attempts")):
        count = int(payload.get("new_attempt_records", 0))
        text = "None." if count == 0 else f"{count} new record(s); details unavailable."
        return [_paint(f"  ○ {text}", "dim", color)]
    return [
        _paint(f"  ◆ {line[2:] if line.startswith('- ') else line}", "yellow", color)
        for line in _new_score_lines(payload)
    ]


def _score_control_lines(payload: dict[str, object]) -> list[str]:
    controls = _object_list(payload.get("score_controls_read"))
    if not controls:
        return ["None were available."]
    return [f"- {_lesson(control)}: {_attempt_history_text(control)}" for control in controls]


def _score_control_heading(payload: dict[str, object]) -> str:
    controls = _object_list(payload.get("score_controls_read"))
    source = (
        "LIVE SCORE CONTROLS OPENED"
        if payload.get("score_scan_mode") == "live_all_available"
        else "SCORE HISTORIES READ"
    )
    return f"{source} ({len(controls)})"


def _terminal_score_controls(payload: dict[str, object], *, color: bool) -> list[str]:
    controls = _object_list(payload.get("score_controls_read"))
    if not controls:
        return [_paint("  ○ None were available.", "dim", color)]
    return [
        _paint(f"  • {_lesson(control)}: {_attempt_history_text(control)}", "dim", color)
        for control in controls
    ]


def _attempt_history_text(control: dict[str, object]) -> str:
    attempts = _object_list(control.get("attempts_newest_first"))
    if not attempts:
        return "no attempts shown"
    return ", ".join(
        f"{attempt.get('score', 'unknown')}% on {attempt.get('attempt_date', 'unknown')}"
        for attempt in attempts
    )


def _terminal_actions(
    actions: list[dict[str, object]],
    evidence: dict[tuple[object, object], dict[str, object]],
    *,
    style: str,
    color: bool,
) -> list[str]:
    if not actions:
        return ["  None."]
    lines = []
    for action in actions:
        record = evidence.get((action.get("title"), action.get("variant")))
        lines.extend(
            _paint(line, style, color)
            for line in (
                f"  • {_lesson(action)}",
                f"    Scores: {_scores_text(record.get('scores') if record else None)}",
                f"    Why: {_friendly_reason(action.get('reason'))}",
            )
        )
    return lines


def _terminal_additions(
    additions: list[dict[str, object]],
    mastered: list[dict[str, object]],
    *,
    color: bool,
) -> list[str]:
    if not additions:
        return ["  None."]
    lines = []
    for action in additions:
        lines.extend(
            _paint(line, "blue", color)
            for line in (
                f"  • {_lesson(action)}",
                f"    Why: {_addition_reason(action, mastered)}",
            )
        )
    return lines


def _evidence_index(
    payload: dict[str, object],
) -> dict[tuple[object, object], dict[str, object]]:
    return {
        (item.get("title"), item.get("variant")): item
        for item in _object_list(payload.get("score_evidence"))
    }


def _scores_text(scores: object) -> str:
    if not isinstance(scores, list) or not scores:
        return "not attempted"
    return " → ".join(f"{score}%" for score in scores)


def _friendly_reason(reason: object) -> str:
    text = str(reason or "No reason recorded").replace("_", " ")
    return text[0].upper() + text[1:] if text else text


def _addition_reason(addition: dict[str, object], mastered: list[dict[str, object]]) -> str:
    destination = _lesson(addition)
    for removal in mastered:
        reason = str(removal.get("reason", ""))
        marker = "; promote to "
        if marker in reason and reason.split(marker, 1)[1] == destination:
            return f"Next difficulty after {_lesson(removal)} met the mastery rule."
    return _friendly_reason(addition.get("reason"))


def _queue_state(record: dict[str, object]) -> str:
    scores = record.get("scores")
    if not isinstance(scores, list) or not scores:
        return "NOT ATTEMPTED"
    status = str(record.get("status", "not_mastered"))
    return "PROVISIONAL" if status == "provisional" else "HOLD"


def _outcome(status: str) -> str:
    return {
        "applied": "changes applied and final queue verified",
        "no_op": "no changes needed; live queue verified",
        "review_required": "review complete; changes proposed but not applied",
        "interrupted": "interrupted; only completed actions are shown",
    }.get(status, status)


def _terminal_action_headings(status: str) -> tuple[str, str]:
    if status == "review_required":
        return ("WILL BE UNCHECKED (NOT YET APPLIED)", "WILL BE ADDED (NOT YET APPLIED)")
    if status == "interrupted":
        return ("UNCHECKED BEFORE INTERRUPTION", "ADDED BEFORE INTERRUPTION")
    return ("UNCHECKED", "ADDED")


def _change_summary_lines(
    new_attempts: int,
    mastered: list[dict[str, object]],
    removals: list[dict[str, object]],
    additions: list[dict[str, object]],
    *,
    color: bool,
) -> list[str]:
    if new_attempts == 0 and not removals and not additions:
        return [_paint("  ○ No new lesson attempts or assignment changes.", "dim", color)]
    return [
        _paint(f"  ◆ New attempts: {new_attempts}", "yellow", color),
        _paint(f"  ✓ Mastered: {len(mastered)}", "green", color),
        _paint(f"  − Unchecked: {len(removals)}", "magenta", color),
        _paint(f"  + Added: {len(additions)}", "blue", color),
    ]


def _outcome_style(status: str) -> str:
    return {
        "applied": "green",
        "no_op": "dim",
        "review_required": "yellow",
        "interrupted": "red",
    }.get(status, "bold")


def _paint(text: str, style: str, enabled: bool) -> str:
    if not enabled:
        return text
    codes = {
        "bold": "1",
        "dim": "2",
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
    }
    return f"\033[{codes[style]}m{text}\033[0m"
