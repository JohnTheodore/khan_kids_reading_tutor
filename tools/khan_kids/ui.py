"""Small, dependency-free helpers for Android UIAutomator hierarchies."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass

VARIANT_ORDER = ("Main", "Practice 1", "Practice 2", "Basic")


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]

    def as_bounds(self) -> str:
        return f"[{self.left},{self.top}][{self.right},{self.bottom}]"


@dataclass(frozen=True, slots=True)
class UiText:
    text: str
    rect: Rect
    node: ET.Element


def parse_rect(raw: str) -> Rect | None:
    values = [int(value) for value in re.findall(r"\d+", raw)]
    return Rect(*values) if len(values) == 4 else None


def node_rect(node: ET.Element) -> Rect | None:
    return parse_rect(node.attrib.get("bounds", ""))


def near(value: int, expected: int, *, tolerance: int = 2) -> bool:
    """Allow harmless pixel-rounding differences between app renders."""
    return abs(value - expected) <= tolerance


def visible_nodes(root: ET.Element) -> list[UiText]:
    result: list[UiText] = []
    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()
        rect = node_rect(node)
        if text and rect is not None:
            result.append(UiText(text=text, rect=rect, node=node))
    return result


def find_text(root: ET.Element, text: str) -> list[UiText]:
    return [item for item in visible_nodes(root) if item.text == text]


def text_set(root: ET.Element) -> set[str]:
    return {item.text for item in visible_nodes(root)}


def unique_text(root: ET.Element, text: str) -> UiText:
    matches = find_text(root, text)
    if len(matches) != 1:
        raise ValueError(f"Expected one {text!r} node, found {len(matches)}")
    return matches[0]


def deduplicate_nodes(items: Iterable[UiText]) -> list[UiText]:
    unique = {(item.text, item.rect): item for item in items}
    return sorted(unique.values(), key=lambda item: (item.rect.top, item.rect.left))
