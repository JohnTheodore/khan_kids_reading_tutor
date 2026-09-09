"""Pure mastery-policy decisions, independent of Android and file formats."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

LEARNING_SEQUENCE = ("Basic", "Main", "Practice 1", "Practice 2")


class MasteryStatus(StrEnum):
    MASTERED = "mastered"
    PROVISIONAL = "provisional"
    NOT_MASTERED = "not_mastered"


@dataclass(frozen=True, slots=True)
class MasteryDecision:
    status: MasteryStatus
    scores: tuple[int, ...]
    reason: str

    @property
    def should_advance(self) -> bool:
        return self.status is MasteryStatus.MASTERED


def evaluate_mastery(scores: Iterable[int]) -> MasteryDecision:
    ordered = tuple(scores)
    if not ordered:
        return MasteryDecision(MasteryStatus.NOT_MASTERED, ordered, "no completed attempts")
    latest = ordered[-1]
    if latest == 100:
        return MasteryDecision(MasteryStatus.MASTERED, ordered, "latest attempt is 100%")
    if len(ordered) >= 2 and ordered[-2] >= 90 and latest >= 90:
        return MasteryDecision(
            MasteryStatus.MASTERED,
            ordered,
            "two consecutive attempts are at least 90%",
        )
    if latest >= 90:
        return MasteryDecision(
            MasteryStatus.PROVISIONAL,
            ordered,
            "one qualifying attempt; another score of at least 90% is required",
        )
    return MasteryDecision(
        MasteryStatus.NOT_MASTERED,
        ordered,
        f"latest attempt is below 90% ({latest}%)",
    )


def next_variant(current: str, available: Sequence[str]) -> str | None:
    available_set = set(available)
    try:
        start = LEARNING_SEQUENCE.index(current) + 1
    except ValueError as error:
        raise ValueError(f"Unknown activity variant: {current!r}") from error
    return next(
        (variant for variant in LEARNING_SEQUENCE[start:] if variant in available_set),
        None,
    )
