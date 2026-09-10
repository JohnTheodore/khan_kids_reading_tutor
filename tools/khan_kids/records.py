"""Append-only, duplicate-safe CSV records for attempts and assignment actions."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
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
) -> list[dict[str, str]]:
    """Atomically append unique records and return the normalized additions."""
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
    identities = {tuple(record[field] for field in identity_fields) for record in existing}
    additions: list[dict[str, str]] = []
    for row in rows:
        normalized = {field: str(row.get(field, "")) for field in fieldnames}
        identity = tuple(normalized[field] for field in identity_fields)
        if identity not in identities:
            identities.add(identity)
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
            }
        ],
        identity_fields=("action_date", "student", "action", "lesson_title", "activity_variant"),
    )


def read_attempt_scores(path: Path, student: str) -> dict[tuple[str, str], tuple[int, ...]]:
    """Load chronological score sequences from the append-only attempt record."""
    if not path.exists():
        return {}
    grouped: dict[tuple[str, str], list[tuple[date, int, int]]] = {}
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
            key = (row["lesson_title"], row["activity_variant"])
            grouped.setdefault(key, []).append(
                (date.fromisoformat(row["attempt_date"]), order, int(row["score_percent"]))
            )
    return {
        key: tuple(score for _attempt_date, _order, score in sorted(attempts))
        for key, attempts in grouped.items()
    }
