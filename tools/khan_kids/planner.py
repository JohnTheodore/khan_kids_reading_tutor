"""Pure desired-state planning for a small, mastery-gated reading queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from .curriculum import Activity, ReadingCurriculum, Track
from .mastery import MasteryDecision, MasteryStatus, evaluate_mastery
from .reports import AssignmentSnapshot


@dataclass(frozen=True, slots=True)
class TrackState:
    track_id: str
    complete: bool
    unlocked: bool
    next_activity: Activity | None
    decision: MasteryDecision | None


@dataclass(frozen=True, slots=True)
class QueueAction:
    kind: Literal["add", "remove"]
    title: str
    variant: str
    grade: str
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.title, self.variant)

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "title": self.title,
            "variant": self.variant,
            "grade": self.grade,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> QueueAction:
        kind = payload.get("kind")
        if kind not in {"add", "remove"}:
            raise ValueError(f"invalid queue action kind: {kind!r}")
        values = {field: payload.get(field) for field in ("title", "variant", "grade", "reason")}
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("queue action fields must be strings")
        return cls(kind, values["title"], values["variant"], values["grade"], values["reason"])


@dataclass(frozen=True, slots=True)
class QueuePlan:
    desired: tuple[Activity, ...]
    actions: tuple[QueueAction, ...]
    tracks: tuple[TrackState, ...]


def build_queue_plan(
    curriculum: ReadingCurriculum,
    scores: dict[tuple[str, str], tuple[int, ...]],
    current: set[tuple[str, str]],
) -> QueuePlan:
    preliminary = {track.track_id: _evaluate_track(track, scores) for track in curriculum.tracks}
    complete = {track_id for track_id, state in preliminary.items() if state.complete}
    states: list[TrackState] = []
    candidates: list[Activity] = []
    reasons: dict[tuple[str, str], str] = {}
    for track in curriculum.tracks:
        state = preliminary[track.track_id]
        unlocked = all(requirement in complete for requirement in track.requires)
        state = TrackState(
            state.track_id,
            state.complete,
            unlocked,
            state.next_activity,
            state.decision,
        )
        states.append(state)
        if unlocked and not state.complete and state.next_activity is not None:
            candidates.append(state.next_activity)
            reasons[state.next_activity.key] = _target_reason(track, state)

    desired = tuple(candidates[: curriculum.queue_limit])
    desired_by_key = {activity.key: activity for activity in desired}
    desired_keys = set(desired_by_key)
    removals = tuple(
        QueueAction("remove", title, variant, "", "not in the mastery-gated desired queue")
        for title, variant in sorted(current - desired_keys)
    )
    additions = tuple(
        QueueAction(
            "add",
            activity.title,
            activity.variant,
            activity.grade,
            reasons[activity.key],
        )
        for activity in desired
        if activity.key not in current
    )
    return QueuePlan(desired, removals + additions, tuple(states))


def snapshot_fingerprint(snapshot: AssignmentSnapshot) -> str:
    rows = sorted((row.title, row.variant, row.assigned_date, row.score) for row in snapshot.rows)
    histories = sorted(
        (
            history.title,
            history.variant,
            history.assigned_date.isoformat(),
            tuple(
                (attempt.attempt_date.isoformat(), attempt.score)
                for attempt in history.attempts_newest_first
            ),
        )
        for history in snapshot.histories
    )
    encoded = json.dumps(
        {"rows": rows, "histories": histories}, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _evaluate_track(track: Track, scores: dict[tuple[str, str], tuple[int, ...]]) -> TrackState:
    for activity in track.activities:
        decision = evaluate_mastery(scores.get(activity.key, ()))
        if decision.status is not MasteryStatus.MASTERED:
            return TrackState(track.track_id, False, False, activity, decision)
    return TrackState(track.track_id, True, False, None, None)


def _target_reason(track: Track, state: TrackState) -> str:
    if state.decision is None:
        return f"next activity in {track.track_id}"
    if not state.decision.scores:
        return f"first unmastered activity in {track.track_id}"
    return f"hold {track.track_id}: {state.decision.reason}"
