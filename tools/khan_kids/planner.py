"""Pure desired-state planning for a small, mastery-gated reading queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from .curriculum import Activity, DiversityGroup, ReadingCurriculum, StretchTrack, Track
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
    candidates: list[tuple[Track, TrackState]] = []
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
            candidates.append((track, state))
            reasons[state.next_activity.key] = _target_reason(track, state)

    selected_track_ids = _select_diverse_tracks(curriculum, candidates)
    group_by_track = _group_by_track(curriculum)
    diversity_holds = {
        state.next_activity.key: group_by_track[track.track_id].max_active
        for track, state in candidates
        if track.track_id not in selected_track_ids and state.next_activity is not None
    }
    core_limit = curriculum.queue_limit - curriculum.stretch_slots
    core_desired = tuple(
        state.next_activity
        for track, state in candidates
        if track.track_id in selected_track_ids and state.next_activity is not None
    )[:core_limit]
    stretch_desired = _select_stretch_activities(
        curriculum,
        scores,
        current,
        complete,
        excluded={activity.key for activity in core_desired},
        limit=curriculum.queue_limit - len(core_desired),
    )
    for activity in stretch_desired:
        reasons[activity.key] = "next mastery rung in a rotating stretch slot"
    desired = core_desired + stretch_desired
    desired_by_key = {activity.key: activity for activity in desired}
    desired_keys = set(desired_by_key)
    configured = {
        activity.key: (track, position, activity)
        for track in curriculum.tracks
        for position, activity in enumerate(track.activities)
    }
    stretch_locations = {
        activity.key: (stretch, position, activity)
        for stretch in curriculum.stretch_pool
        for position, activity in enumerate(stretch.activities)
    }
    removals = tuple(
        QueueAction(
            "remove",
            title,
            variant,
            _activity_grade((title, variant), configured, stretch_locations),
            _removal_reason(
                (title, variant),
                configured,
                stretch_locations,
                scores,
                desired_keys,
                diversity_holds,
            ),
        )
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


def _select_stretch_activities(
    curriculum: ReadingCurriculum,
    scores: dict[tuple[str, str], tuple[int, ...]],
    current: set[tuple[str, str]],
    complete_tracks: set[str],
    *,
    excluded: set[tuple[str, str]],
    limit: int,
) -> tuple[Activity, ...]:
    candidates = [
        (stretch, activity, decision)
        for stretch in curriculum.stretch_pool
        if (evaluated := _evaluate_stretch(stretch, scores)) is not None
        for activity, decision in (evaluated,)
        if activity.key not in excluded
    ]
    selected: list[Activity] = []
    # Once exposed, an unattempted or adequately placed stretch lesson is pinned.
    for stretch, activity, decision in candidates:
        family_is_active = any(item.key in current for item in stretch.activities)
        if family_is_active and (not decision.scores or decision.scores[-1] >= 70):
            selected.append(activity)
            if len(selected) == limit:
                return tuple(selected)
    selected_keys = {activity.key for activity in selected}
    for stretch, activity, decision in candidates:
        if activity.key in selected_keys:
            continue
        if _stretch_is_eligible(stretch, decision, complete_tracks):
            selected.append(activity)
            selected_keys.add(activity.key)
            if len(selected) == limit:
                break
    return tuple(selected)


def _evaluate_stretch(
    stretch: StretchTrack, scores: dict[tuple[str, str], tuple[int, ...]]
) -> tuple[Activity, MasteryDecision] | None:
    for activity in stretch.activities:
        decision = evaluate_mastery(scores.get(activity.key, ()))
        if decision.status is not MasteryStatus.MASTERED:
            return activity, decision
    return None


def _stretch_is_eligible(
    stretch: StretchTrack, decision: MasteryDecision, complete_tracks: set[str]
) -> bool:
    if not decision.scores or decision.scores[-1] >= 70:
        return True
    attempts_below_floor = sum(score < 70 for score in decision.scores)
    attempt_allowance = 1 + sum(
        milestone in complete_tracks for milestone in stretch.retry_milestones
    )
    return attempts_below_floor < attempt_allowance


def _select_diverse_tracks(
    curriculum: ReadingCurriculum, candidates: list[tuple[Track, TrackState]]
) -> set[str]:
    """Apply configured caps without filling the queue with weaker substitutes."""
    group_by_track = _group_by_track(curriculum)
    grouped: dict[str, list[tuple[Track, TrackState]]] = {}
    selected = {
        track.track_id for track, _state in candidates if track.track_id not in group_by_track
    }
    for track, state in candidates:
        group = group_by_track.get(track.track_id)
        if group is not None:
            grouped.setdefault(group.group_id, []).append((track, state))
    for group in curriculum.diversity_groups:
        ranked = sorted(grouped.get(group.group_id, ()), key=_diversity_rank)
        selected.update(track.track_id for track, _state in ranked[: group.max_active])
    return selected


def _group_by_track(curriculum: ReadingCurriculum) -> dict[str, DiversityGroup]:
    return {
        track_id: group for group in curriculum.diversity_groups for track_id in group.track_ids
    }


def _diversity_rank(candidate: tuple[Track, TrackState]) -> tuple[int, int, int, str]:
    """Prefer active learning evidence, then established progress and curriculum order."""
    track, state = candidate
    decision = state.decision
    latest = decision.scores[-1] if decision and decision.scores else None
    in_instructional_band = latest is not None and 80 <= latest < 100
    progress = track.activities.index(state.next_activity) if state.next_activity else -1
    return (not in_instructional_band, -progress, track.priority, track.track_id)


def _activity_grade(
    key: tuple[str, str],
    configured: dict[tuple[str, str], tuple[Track, int, Activity]],
    stretch_locations: dict[tuple[str, str], tuple[StretchTrack, int, Activity]],
) -> str:
    if key in configured:
        return configured[key][2].grade
    if key in stretch_locations:
        return stretch_locations[key][2].grade
    return ""


def _removal_reason(
    key: tuple[str, str],
    configured: dict[tuple[str, str], tuple[Track, int, Activity]],
    stretch_locations: dict[tuple[str, str], tuple[StretchTrack, int, Activity]],
    scores: dict[tuple[str, str], tuple[int, ...]],
    desired_keys: set[tuple[str, str]],
    diversity_holds: dict[tuple[str, str], int],
) -> str:
    if key in diversity_holds:
        limit = diversity_holds[key]
        return f"deferred: active instructional group is limited to {limit} lessons"
    location = configured.get(key)
    stretch_location = stretch_locations.get(key)
    if stretch_location is not None:
        stretch, position, _activity = stretch_location
        decision = evaluate_mastery(scores.get(key, ()))
        if decision.status is MasteryStatus.MASTERED:
            if position + 1 == len(stretch.activities):
                return f"mastered: {decision.reason}; stretch lesson family complete"
            successor = stretch.activities[position + 1]
            if successor.key in desired_keys:
                return (
                    f"mastered: {decision.reason}; promote to "
                    f"{successor.title} — {successor.variant}"
                )
        if decision.scores and decision.scores[-1] < 70:
            return (
                f"deferred for retry: latest stretch attempt is below 70% "
                f"({decision.scores[-1]}%); waiting for supporting mastery"
            )
        if location is None:
            return "waiting for an open stretch slot"
    if location is None:
        return "not in the approved reading path"
    track, position, _activity = location
    decision = evaluate_mastery(scores.get(key, ()))
    if decision.status is not MasteryStatus.MASTERED:
        return "not in the mastery-gated desired queue"
    if position + 1 == len(track.activities):
        return f"mastered: {decision.reason}; selected lesson family complete"
    successor = track.activities[position + 1]
    if successor.key in desired_keys:
        return f"mastered: {decision.reason}; promote to {successor.title} — {successor.variant}"
    return f"mastered: {decision.reason}; successor waiting for an open queue slot"


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
