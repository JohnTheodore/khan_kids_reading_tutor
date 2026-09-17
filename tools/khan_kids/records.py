"""Append-only, duplicate-safe CSV records for attempts and assignment actions."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

ATTEMPT_FIELDS = (
    "student",
    "attempt_date",
    "assignment_date",
    "lesson_title",
    "activity_variant",
    "report_grade",
    "domain",
    "skill_group",
    "score_percent",
    "source",
    "captured_at",
)
ATTEMPT_ID_FIELDS = (
    "student",
    "attempt_date",
    "lesson_title",
    "activity_variant",
    "score_percent",
)
ACTION_FIELDS = (
    "action_date",
    "student",
    "action",
    "lesson_title",
    "activity_variant",
    "reason",
    "result",
    "recorded_at",
)


def write_json_atomic(path: Path, payload: object) -> None:
    write_text_atomic(path, json.dumps(payload, indent=2) + "\n")


def write_text_atomic(path: Path, content: str) -> None:
    """Replace a UTF-8 text file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    os.replace(temporary, path)


def append_text_atomic(path: Path, content: str) -> None:
    """Append text by atomically replacing the complete file."""
    existing = path.read_text() if path.exists() else ""
    write_text_atomic(path, existing + content)


def append_unique_rows(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
    *,
    identity_fields: Sequence[str],
) -> int:
    """Atomically append records whose selected identity is not already present."""
    return len(
        append_unique_rows_with_records(
            path,
            fieldnames,
            rows,
            identity_fields=identity_fields,
        )
    )


def append_unique_rows_with_records(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
    *,
    identity_fields: Sequence[str],
    reconcile_occurrences: bool = False,
) -> list[dict[str, str]]:
    """Append missing identities, optionally preserving their observed multiplicity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, str]] = []
    if path.exists():
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fieldnames):
                raise ValueError(
                    f"Unexpected columns in {path}: {reader.fieldnames!r}; expected {list(fieldnames)!r}"
                )
            existing = list(reader)
    existing_counts = Counter(
        tuple(record.get(field) or "" for field in identity_fields) for record in existing
    )
    observed_counts: Counter[tuple[str, ...]] = Counter()
    additions: list[dict[str, str]] = []
    for row in rows:
        normalized = {field: str(row.get(field, "")) for field in fieldnames}
        identity = tuple(normalized[field] for field in identity_fields)
        observed_counts[identity] += 1
        if (
            observed_counts[identity] > existing_counts[identity]
            if reconcile_occurrences
            else existing_counts[identity] == 0 and observed_counts[identity] == 1
        ):
            additions.append(normalized)
    if not additions:
        return []
    with tempfile.NamedTemporaryFile(
        "w", newline="", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(additions)
    os.replace(temporary, path)
    return additions


def record_action(
    path: Path,
    *,
    action_date: date,
    student: str,
    action: str,
    title: str,
    variant: str,
    reason: str,
    result: str,
    recorded_at: datetime | None = None,
) -> None:
    append_unique_rows(
        path,
        ACTION_FIELDS,
        [
            {
                "action_date": action_date.isoformat(),
                "student": student,
                "action": action,
                "lesson_title": title,
                "activity_variant": variant,
                "reason": reason,
                "result": result,
                "recorded_at": (recorded_at or datetime.now().astimezone()).isoformat(
                    timespec="microseconds"
                ),
            }
        ],
        identity_fields=("recorded_at",),
    )


def read_mastered_action_keys(path: Path, student: str) -> set[tuple[str, str]]:
    """Return every lesson variant whose successful removal recorded mastery."""
    if not path.exists():
        return set()
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(ACTION_FIELDS):
            raise ValueError(
                f"Unexpected columns in {path}: {reader.fieldnames!r}; "
                f"expected {list(ACTION_FIELDS)!r}"
            )
        return {
            (row["lesson_title"], row["activity_variant"])
            for row in reader
            if row["student"] == student
            and row["action"] == "unchecked"
            and row["reason"].casefold().startswith("mastered:")
            and row["result"].casefold().startswith("saved")
        }


def read_attempt_scores(path: Path, student: str) -> dict[tuple[str, str], tuple[int, ...]]:
    """Load chronological score sequences from the append-only attempt record."""
    grouped: dict[tuple[str, str], list[int]] = {}
    for row in read_attempt_records(path, student):
        key = (row["lesson_title"], row["activity_variant"])
        grouped.setdefault(key, []).append(int(row["score_percent"]))
    return {key: tuple(scores) for key, scores in grouped.items()}


def read_attempt_records(path: Path, student: str) -> list[dict[str, str]]:
    """Read validated chronological attempts without collapsing occurrences."""
    if not path.exists():
        return []
    ordered: list[tuple[date, int, dict[str, str]]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(ATTEMPT_FIELDS):
            raise ValueError(
                f"Unexpected columns in {path}: {reader.fieldnames!r}; "
                f"expected {list(ATTEMPT_FIELDS)!r}"
            )
        for order, row in enumerate(reader):
            if row["student"] != student:
                continue
            score = int(row["score_percent"])
            if not 0 <= score <= 100:
                raise ValueError("Attempt score outside 0–100")
            ordered.append((date.fromisoformat(row["attempt_date"]), order, row))
    return [row for _day, _order, row in sorted(ordered)]
