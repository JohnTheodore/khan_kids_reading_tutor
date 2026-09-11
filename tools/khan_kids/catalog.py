"""Lookup paths and available variants in the archived ELA curriculum."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .adb import AutomationError
from .constants import CURRICULUM_PATH_GRADE_TOKENS


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    grade: str
    domain: str
    skill_group: str
    title: str
    variants: tuple[str, ...]


class CatalogIndex:
    def __init__(self, path: Path) -> None:
        payload = json.loads(path.read_text())
        self.roster = tuple(payload.get("students", ()))
        entries: list[CatalogEntry] = []
        for grade in payload["grades"]:
            for placement in grade["lesson_placements"]:
                variants = tuple(activity["variant"] for activity in placement["activities"])
                if not variants:
                    variants = ("Direct",)
                entries.append(
                    CatalogEntry(
                        grade=_clean_grade(grade["grade"]),
                        domain=placement["domain"] or "",
                        skill_group=placement["skill_group"] or "",
                        title=placement["title"],
                        variants=variants,
                    )
                )
        self.entries = tuple(entries)
        self._order = {
            (entry.grade, entry.title): position for position, entry in enumerate(self.entries)
        }

    def find(self, title: str, variant: str, curriculum_path: str = "") -> CatalogEntry:
        matches = [
            entry for entry in self.entries if entry.title == title and variant in entry.variants
        ]
        if curriculum_path:
            grade_matches = [
                entry for entry in matches if _grade_token(entry.grade) in curriculum_path
            ]
            if grade_matches:
                matches = grade_matches
            group_matches = [entry for entry in matches if entry.skill_group in curriculum_path]
            if group_matches:
                matches = group_matches
        unique = {
            (entry.grade, entry.domain, entry.skill_group, entry.title, entry.variants): entry
            for entry in matches
        }
        if len(unique) != 1:
            raise AutomationError(
                f"Expected one catalog placement for {title!r}/{variant!r}, found {len(unique)}"
            )
        return next(iter(unique.values()))

    def find_exact(self, grade: str, title: str, variant: str) -> CatalogEntry:
        entry = self.find_title_exact(grade, title)
        if variant not in entry.variants:
            raise AutomationError(
                f"Catalog placement {grade!r}/{title!r} has no {variant!r} variant"
            )
        return entry

    def find_title_exact(self, grade: str, title: str) -> CatalogEntry:
        matches = [entry for entry in self.entries if entry.grade == grade and entry.title == title]
        if len(matches) != 1:
            raise AutomationError(
                f"Expected one catalog placement for {grade!r}/{title!r}, found {len(matches)}"
            )
        return matches[0]

    def order_key(self, grade: str, title: str) -> int:
        """Return the archived All Progress position for an exact lesson placement."""
        self.find_title_exact(grade, title)
        return self._order[(grade, title)]


def _grade_token(grade: str) -> str:
    return CURRICULUM_PATH_GRADE_TOKENS.get(grade, grade)


def _clean_grade(grade: str) -> str:
    return grade.removesuffix(": ELA")
