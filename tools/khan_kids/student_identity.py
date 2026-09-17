"""Private real-name mapping at the input boundary; public data uses aliases."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .ui import visible_nodes

ALIAS_PATH = Path(__file__).resolve().parents[2] / "private/student-aliases.local.json"


def require_aliases(aliases: dict[str, str]) -> None:
    if not aliases:
        raise ValueError("A nonempty private student alias map is required before UI capture")


def load_aliases() -> dict[str, str]:
    if not ALIAS_PATH.exists():
        return {}
    data = json.loads(ALIAS_PATH.read_text())
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or not key or not isinstance(value, str) or not value
        for key, value in data.items()
    ):
        raise ValueError("Private student aliases must map nonempty names to nonempty aliases")
    if len({key.casefold() for key in data}) != len(data):
        raise ValueError("Student names must be unique ignoring case")
    if any(not re.fullmatch(r"Student [A-Z]+", value) for value in data.values()):
        raise ValueError("Public student aliases must use the form Student A")
    if len(set(data.values())) != len(data):
        raise ValueError("Student aliases must be unique")
    return data


def public_student(name: str) -> str:
    result = next(
        (alias for real, alias in load_aliases().items() if real.casefold() == name.casefold()),
        name,
    )
    if result != "unknown" and not re.fullmatch(r"Student [A-Z]+", result):
        raise ValueError("Student identity has no configured public alias")
    return result


def anonymize_text(text: str, aliases: dict[str, str]) -> str:
    require_aliases(aliases)
    lookup = {real.casefold(): alias for real, alias in aliases.items()}
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(name) for name in aliases) + r")(?!\w)"
    return re.sub(
        pattern, lambda match: lookup[match.group().casefold()], text, flags=re.IGNORECASE
    )


def validate_identity_view(root: ET.Element, aliases: dict[str, str]) -> None:
    """Reject unknown identities in supported score and student-selection views."""
    allowed = set(aliases.values())
    items = visible_nodes(root)
    for item in items:
        match = re.fullmatch(r"(.+?)(?:'s|’s) Lesson Scores", item.text)
        if match and match[1] not in allowed:
            raise ValueError("Score dialog student has no configured private alias")
    if "Select Students" not in {item.text for item in items}:
        return
    labels = [item for item in items if item.text in allowed and item.rect.top > 400]
    if not labels:
        raise ValueError("Student selection has no configured identity labels")
    for item in items:
        if (
            item.rect.top > 400
            and item.text not in allowed | {"Done", "Select Students", "All Students"}
            and any(abs(item.rect.left - label.rect.left) < 30 for label in labels)
        ):
            raise ValueError("Student selection contains an unconfigured identity label")
