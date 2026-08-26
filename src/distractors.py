from __future__ import annotations

import random
from collections.abc import Iterable, MutableSequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from src.club_data import Club

T = TypeVar("T")


class Randomizer(Protocol):
    def shuffle(self, x: MutableSequence[T], /) -> None: ...


class ChoicePoolError(ValueError):
    """Raised when a scope cannot produce a valid four-choice question."""


@dataclass(frozen=True, slots=True)
class ChoiceSet:
    target: Club
    choices: tuple[Club, ...]

    def __post_init__(self) -> None:
        ids = [club.id for club in self.choices]
        names = [club.name.casefold() for club in self.choices]
        if len(ids) != 4 or len(set(ids)) != 4:
            raise ChoicePoolError("A choice set must contain exactly four unique clubs")
        if len(set(names)) != 4:
            raise ChoicePoolError(
                "A choice set must contain exactly four distinct club names"
            )
        if self.target.id not in ids:
            raise ChoicePoolError("A choice set must contain its target exactly once")


class TargetPool:
    """Draw shuffled targets without replacement, independently per game scope."""

    def __init__(
        self,
        clubs: Iterable[Club],
        *,
        rng: Randomizer | None = None,
    ) -> None:
        self._clubs = tuple(clubs)
        if not self._clubs:
            raise ChoicePoolError("At least one club is required")
        if len({club.id for club in self._clubs}) != len(self._clubs):
            raise ChoicePoolError("Club ids must be unique")
        self._rng = rng or random.Random()
        self._remaining: dict[str, list[Club]] = {}

    def next_target(self, scope: str = "all") -> Club:
        eligible = self._eligible(scope)
        remaining = self._remaining.get(scope)
        if not remaining:
            remaining = list(eligible)
            self._rng.shuffle(remaining)
            self._remaining[scope] = remaining
        return remaining.pop()

    def reset(self, scope: str | None = None) -> None:
        if scope is None:
            self._remaining.clear()
        else:
            self._remaining.pop(scope, None)

    def _eligible(self, scope: str) -> tuple[Club, ...]:
        if scope == "all":
            return self._clubs
        eligible = tuple(club for club in self._clubs if club.scope == scope)
        if not eligible:
            raise ChoicePoolError(f"No clubs are available for scope {scope!r}")
        return eligible


def build_choices(
    target: Club,
    clubs: Iterable[Club],
    scope: str,
    *,
    rng: Randomizer | None = None,
) -> ChoiceSet:
    """Build exactly four shuffled choices according to the selected mode."""

    randomizer = rng or random.Random()
    all_clubs = tuple(clubs)
    if len({club.id for club in all_clubs}) != len(all_clubs):
        raise ChoicePoolError("Club ids must be unique")
    if target.id not in {club.id for club in all_clubs}:
        raise ChoicePoolError("The target is not present in the club pool")

    if scope == "all":
        candidates = [
            club
            for club in all_clubs
            if club.id != target.id and club.name.casefold() != target.name.casefold()
        ]
        distractors = _take_random(candidates, 3, randomizer)
        if distractors and all(club.scope == target.scope for club in distractors):
            cross_league_candidates = [
                club
                for club in candidates
                if club.scope != target.scope
                and club.name.casefold()
                not in {choice.name.casefold() for choice in distractors}
            ]
            replacement = _take_random(cross_league_candidates, 1, randomizer)
            if not replacement:
                raise ChoicePoolError(
                    "All Leagues mode needs a distractor from another league"
                )
            distractors[-1] = replacement[0]
    else:
        if target.scope != scope:
            raise ChoicePoolError(
                f"Target scope {target.scope!r} does not match mode {scope!r}"
            )
        candidates = [
            club
            for club in all_clubs
            if club.id != target.id
            and club.name.casefold() != target.name.casefold()
            and club.scope == scope
        ]
        distractors = _take_random(candidates, 3, randomizer)

    if len(distractors) != 3:
        raise ChoicePoolError(
            f"Scope {scope!r} needs at least four eligible clubs for a question"
        )

    choices = [target, *distractors]
    randomizer.shuffle(choices)
    return ChoiceSet(target=target, choices=tuple(choices))


def select_distractors(
    target: Club,
    clubs: Iterable[Club],
    scope: str,
    *,
    rng: Randomizer | None = None,
) -> tuple[Club, ...]:
    """Return only the three distractors for callers that do their own rendering."""

    choice_set = build_choices(target, clubs, scope, rng=rng)
    return tuple(club for club in choice_set.choices if club.id != target.id)


def _take_random(
    candidates: list[Club], count: int, randomizer: Randomizer
) -> list[Club]:
    if count <= 0:
        return []
    shuffled = list(candidates)
    randomizer.shuffle(shuffled)
    selected: list[Club] = []
    selected_names: set[str] = set()
    for candidate in shuffled:
        normalized_name = candidate.name.casefold()
        if normalized_name in selected_names:
            continue
        selected.append(candidate)
        selected_names.add(normalized_name)
        if len(selected) == count:
            break
    return selected
