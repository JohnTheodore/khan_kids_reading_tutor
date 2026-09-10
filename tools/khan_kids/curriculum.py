"""Validated, machine-readable reading sequences built from archived Khan lessons."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .catalog import CatalogIndex
from .constants import LEARNING_SEQUENCE


@dataclass(frozen=True, slots=True)
class Activity:
    grade: str
    title: str
    variant: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.title, self.variant)

    def as_dict(self) -> dict[str, str]:
        return {"grade": self.grade, "title": self.title, "variant": self.variant}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Activity:
        return cls(
            grade=_required_string(payload, "grade"),
            title=_required_string(payload, "title"),
            variant=_required_string(payload, "variant"),
        )


@dataclass(frozen=True, slots=True)
class Track:
    track_id: str
    priority: int
    requires: tuple[str, ...]
    activities: tuple[Activity, ...]


@dataclass(frozen=True, slots=True)
class DiversityGroup:
    group_id: str
    max_active: int
    track_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadingCurriculum:
    path_id: str
    name: str
    objective: str
    scope: str
    entry_criteria: tuple[str, ...]
    segment_exit_criteria: tuple[str, ...]
    queue_limit: int
    diversity_groups: tuple[DiversityGroup, ...]
    tracks: tuple[Track, ...]

    @property
    def activities_by_key(self) -> dict[tuple[str, str], Activity]:
        return {activity.key: activity for track in self.tracks for activity in track.activities}

    @classmethod
    def load(cls, path: Path, catalog: CatalogIndex) -> ReadingCurriculum:
        payload = json.loads(path.read_text())
        if payload.get("version") != 1:
            raise ValueError(f"Unsupported curriculum version in {path}")
        path_id = _required_string(payload, "path_id")
        name = _required_string(payload, "name")
        objective = _required_string(payload, "objective")
        scope = _required_string(payload, "scope")
        entry_criteria = _string_list(payload, "entry_criteria", required=True)
        segment_exit_criteria = _string_list(payload, "segment_exit_criteria", required=True)
        queue_limit = payload.get("queue_limit")
        if not isinstance(queue_limit, int) or queue_limit < 1:
            raise ValueError("queue_limit must be a positive integer")
        raw_tracks = payload.get("tracks")
        if not isinstance(raw_tracks, list) or not raw_tracks:
            raise ValueError("tracks must be a non-empty list")

        tracks: list[Track] = []
        activity_owners: dict[tuple[str, str], str] = {}
        for raw_track in raw_tracks:
            if not isinstance(raw_track, dict):
                raise ValueError("each track must be an object")
            track_id = _required_string(raw_track, "id")
            priority = raw_track.get("priority")
            if not isinstance(priority, int):
                raise ValueError(f"track {track_id!r} priority must be an integer")
            requires = _string_list(raw_track, "requires")
            raw_groups = raw_track.get("lesson_groups")
            if not isinstance(raw_groups, list) or not raw_groups:
                raise ValueError(f"track {track_id!r} must contain lesson_groups")
            activities: list[Activity] = []
            for raw_group in raw_groups:
                if not isinstance(raw_group, dict):
                    raise ValueError(f"track {track_id!r} contains a non-object lesson group")
                grade = _required_string(raw_group, "grade")
                title = _required_string(raw_group, "title")
                entry = catalog.find_title_exact(grade, title)
                variants = tuple(
                    variant for variant in LEARNING_SEQUENCE if variant in entry.variants
                )
                if not variants:
                    raise ValueError(f"track {track_id!r}/{title!r} has no assignable variants")
                for variant in variants:
                    activity = Activity(grade, title, variant)
                    owner = activity_owners.get(activity.key)
                    if owner is not None:
                        raise ValueError(
                            f"activity {activity.key!r} is duplicated in tracks "
                            f"{owner!r} and {track_id!r}"
                        )
                    activity_owners[activity.key] = track_id
                    activities.append(activity)
            tracks.append(Track(track_id, priority, requires, tuple(activities)))

        identifiers = [track.track_id for track in tracks]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("track ids must be unique")
        known = set(identifiers)
        for track in tracks:
            missing = set(track.requires) - known
            if missing:
                raise ValueError(f"track {track.track_id!r} has unknown requirements: {missing}")
            if track.track_id in track.requires:
                raise ValueError(f"track {track.track_id!r} cannot require itself")
        _reject_dependency_cycles(tracks)
        diversity_groups = _load_diversity_groups(payload, known)
        return cls(
            path_id,
            name,
            objective,
            scope,
            entry_criteria,
            segment_exit_criteria,
            queue_limit,
            diversity_groups,
            tuple(sorted(tracks, key=lambda track: (track.priority, track.track_id))),
        )


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _string_list(
    payload: dict[str, object], key: str, *, required: bool = False
) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    if required and not value:
        raise ValueError(f"{key} must not be empty")
    return tuple(value)


def _reject_dependency_cycles(tracks: list[Track]) -> None:
    dependencies = {track.track_id: track.requires for track in tracks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(track_id: str) -> None:
        if track_id in visiting:
            raise ValueError(f"curriculum track dependency cycle includes {track_id!r}")
        if track_id in visited:
            return
        visiting.add(track_id)
        for requirement in dependencies[track_id]:
            visit(requirement)
        visiting.remove(track_id)
        visited.add(track_id)

    for track in tracks:
        visit(track.track_id)


def _load_diversity_groups(
    payload: dict[str, object], known_tracks: set[str]
) -> tuple[DiversityGroup, ...]:
    raw_groups = payload.get("diversity_groups", [])
    if not isinstance(raw_groups, list):
        raise ValueError("diversity_groups must be a list")
    groups: list[DiversityGroup] = []
    identifiers: set[str] = set()
    assigned_tracks: set[str] = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            raise ValueError("each diversity group must be an object")
        group_id = _required_string(raw_group, "id")
        max_active = raw_group.get("max_active")
        track_ids = _string_list(raw_group, "track_ids", required=True)
        if group_id in identifiers:
            raise ValueError(f"diversity group id is duplicated: {group_id!r}")
        if not isinstance(max_active, int) or max_active < 1:
            raise ValueError(f"diversity group {group_id!r} max_active must be positive")
        if len(track_ids) != len(set(track_ids)):
            raise ValueError(f"diversity group {group_id!r} contains duplicate tracks")
        missing = set(track_ids) - known_tracks
        if missing:
            raise ValueError(f"diversity group {group_id!r} has unknown tracks: {missing}")
        overlap = assigned_tracks.intersection(track_ids)
        if overlap:
            raise ValueError(f"tracks belong to multiple diversity groups: {overlap}")
        identifiers.add(group_id)
        assigned_tracks.update(track_ids)
        groups.append(DiversityGroup(group_id, max_active, track_ids))
    return tuple(groups)
