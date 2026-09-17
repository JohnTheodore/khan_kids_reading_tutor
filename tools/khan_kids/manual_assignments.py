"""Private parent overrides, composed with the existing mastery queue planner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from .adb import AutomationError
from .catalog import CatalogIndex
from .curriculum import Activity, ReadingCurriculum
from .mastery import evaluate_mastery
from .planner import QueuePlan, build_queue_plan
from .records import write_json_atomic


def policy_path(root: Path, student: str) -> Path:
    return root / "private" / f"{student.casefold().replace(' ', '-')}-manual-assignments.json"


@dataclass(frozen=True)
class ManualChange:
    activity: Activity
    action: str

    @classmethod
    def from_dict(cls, payload: dict, catalog: CatalogIndex) -> ManualChange:
        if set(payload) != {"grade", "title", "variant", "action"}:
            raise AutomationError("Select an exact lesson variant and assign or unassign it")
        if any(not isinstance(value, str) or not value for value in payload.values()):
            raise AutomationError("Assignment fields must be nonempty strings")
        if payload["action"] not in {"assign", "unassign"}:
            raise AutomationError("Assignment action must be assign or unassign")
        activity = Activity.from_dict(payload)
        catalog.find_exact(activity.grade, activity.title, activity.variant)
        return cls(activity, payload["action"])

    def as_dict(self) -> dict:
        return {**self.activity.as_dict(), "action": self.action}


@dataclass(frozen=True)
class ManualAssignments:
    student: str
    assigned: tuple[Activity, ...] = ()
    excluded: tuple[Activity, ...] = ()
    last_change: ManualChange | None = None

    def as_dict(self) -> dict:
        return {
            "version": 1,
            "student": self.student,
            "assigned": [a.as_dict() for a in self.assigned],
            "excluded": [a.as_dict() for a in self.excluded],
            "last_change": self.last_change.as_dict() if self.last_change else None,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()

    @property
    def excluded_keys(self) -> set[tuple[str, str]]:
        return {a.key for a in self.excluded}

    @classmethod
    def load(cls, path: Path, student: str, catalog: CatalogIndex) -> ManualAssignments:
        if not path.exists():
            return cls(student)
        try:
            raw = json.loads(path.read_text())
            if raw["version"] != 1 or raw["student"] != student:
                raise ValueError("Wrong policy version or student")
            groups = []
            for name in ("assigned", "excluded"):
                activities = tuple(Activity.from_dict(item) for item in raw[name])
                if len({a.key for a in activities}) != len(activities):
                    raise ValueError("Duplicate manual variants")
                for activity in activities:
                    catalog.find_exact(activity.grade, activity.title, activity.variant)
                groups.append(activities)
            if {a.key for a in groups[0]} & {a.key for a in groups[1]}:
                raise ValueError("Conflicting assignment overrides")
            change = (
                ManualChange.from_dict(raw["last_change"], catalog)
                if raw.get("last_change")
                else None
            )
            return cls(student, *groups, change)
        except (KeyError, ValueError, TypeError) as error:
            raise AutomationError("Manual assignment preferences could not be validated") from error

    def changed(self, change: ManualChange) -> ManualAssignments:
        groups = [
            {a.key: a for a in self.assigned},
            {a.key: a for a in self.excluded},
        ]
        for group in groups:
            group.pop(change.activity.key, None)
        groups[change.action == "unassign"][change.activity.key] = change.activity
        return replace(
            self,
            assigned=tuple(groups[0][key] for key in sorted(groups[0])),
            excluded=tuple(groups[1][key] for key in sorted(groups[1])),
            last_change=change,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json_atomic(path, self.as_dict())


def plan_parent_queue(
    curriculum: ReadingCurriculum,
    catalog: CatalogIndex,
    scores: dict[tuple[str, str], tuple[int, ...]],
    current: set[tuple[str, str]],
    policy: ManualAssignments,
    *,
    quarantined_titles: dict[str, str] | None = None,
    mastered_keys: set[tuple[str, str]] | None = None,
    change: ManualChange | None = None,
) -> QueuePlan:
    """One parent click changes only its variant; sync composes protected extras.

    Automatic slots are filled only below ten after removals. Parent pins may
    overflow, never displace a healthy automatic slot merely to enforce ten.
    """
    mastered_keys = mastered_keys or set()
    pins = {a.key: a for a in policy.assigned}
    explicit_desired = None
    if change:
        if change != policy.last_change:
            raise AutomationError(
                "Reviewed parent assignment no longer matches its saved preference"
            )
        keys = (
            current | {change.activity.key}
            if change.action == "assign"
            else current - {change.activity.key}
        )
        explicit_desired = []
        for key in sorted(keys):
            activity = pins.get(key) or curriculum.activities_by_key.get(key)
            if not activity:
                # Retained rows are never assigned again by this operation.
                # Their report has no grade column; don't fail or guess a
                # mutation just because a title appears at multiple grades.
                entry = next(
                    (e for e in catalog.entries if e.title == key[0] and key[1] in e.variants), None
                )
                activity = Activity(entry.grade if entry else "Existing assignment", *key)
            explicit_desired.append(activity)
    else:
        pins = {
            key: activity
            for key, activity in pins.items()
            if key not in mastered_keys and not evaluate_mastery(scores.get(key, ())).should_advance
        }

    def protected_desired(automatic: tuple[Activity, ...]) -> tuple[Activity, ...]:
        if explicit_desired is not None:
            return tuple(explicit_desired)
        if not policy.assigned and not policy.excluded:
            return automatic
        desired = [a for a in automatic if a.key in current and a.key not in pins]
        desired.extend(pins.values())
        selected = {a.key for a in desired}
        for activity in automatic:
            if len(desired) >= curriculum.queue_limit:
                break
            if activity.key not in selected:
                desired.append(activity)
                selected.add(activity.key)
        return tuple(desired)

    native = build_queue_plan(
        curriculum,
        scores,
        current,
        quarantined_titles=quarantined_titles,
        mastered_keys=mastered_keys,
        excluded_keys=policy.excluded_keys,
        desired_policy=protected_desired,
    )
    actions = []
    parent_keys = {a.key for a in policy.assigned}
    for action in native.actions:
        if action.kind == "add" and action.key in pins:
            reason = "parent assigned this variant; protected until mastered or unassigned"
        elif action.kind == "remove" and (change or action.key in policy.excluded_keys):
            reason = "parent unassigned this variant; automatic reassignment is paused"
        elif action.kind == "remove" and action.key in parent_keys:
            decision = evaluate_mastery(scores.get(action.key, ()))
            reason = "mastered: " + (
                decision.reason if decision.should_advance else "previously verified mastery"
            )
        else:
            actions.append(action)
            continue
        actions.append(replace(action, reason=reason))
    return replace(native, actions=tuple(actions))
