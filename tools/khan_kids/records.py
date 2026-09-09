"""Append-only, duplicate-safe CSV records for attempts and assignment actions."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def append_unique_rows(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
    *,
    identity_fields: Sequence[str],
) -> int:
    """Atomically append records whose selected identity is not already present."""
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
        return 0
    with tempfile.NamedTemporaryFile(
        "w", newline="", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(additions)
    os.replace(temporary, path)
    return len(additions)
