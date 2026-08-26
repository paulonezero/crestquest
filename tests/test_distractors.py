from __future__ import annotations

import random

import pytest

from src.club_data import Club
from src.distractors import ChoicePoolError, TargetPool, build_choices


def club(
    number: int,
    scope: str = "league-a",
    name: str | None = None,
) -> Club:
    return Club(
        id=f"club-{number}",
        provider_id=number,
        name=name or f"Club {number}",
        scope=scope,
        league=scope.replace("-", " ").title(),
        season="2025/26",
        source_url=f"https://example.test/clubs/{number}",
        crest_path=f"crests/club-{number}.svg",
    )


def test_target_pool_has_no_repeat_before_scope_exhaustion() -> None:
    clubs = [club(number) for number in range(1, 6)]
    pool = TargetPool(clubs, rng=random.Random(4))

    first_cycle = [pool.next_target("league-a").id for _ in clubs]
    next_cycle_target = pool.next_target("league-a").id

    assert len(set(first_cycle)) == len(clubs)
    assert next_cycle_target in set(first_cycle)


def test_individual_league_question_uses_only_that_scope() -> None:
    clubs = [club(number) for number in range(1, 6)] + [
        club(number, "league-b") for number in range(6, 11)
    ]

    result = build_choices(clubs[0], clubs, "league-a", rng=random.Random(2))

    assert len(result.choices) == 4
    assert {choice.scope for choice in result.choices} == {"league-a"}
    assert sum(choice.id == result.target.id for choice in result.choices) == 1


def test_all_mode_draws_from_the_full_club_pool() -> None:
    clubs = [club(1, "league-a"), club(2, "league-a")] + [
        club(number, "league-b") for number in range(3, 7)
    ]

    result = build_choices(clubs[0], clubs, "all", rng=random.Random(1))

    assert any(choice.scope != result.target.scope for choice in result.choices)
    assert len({choice.id for choice in result.choices}) == 4


def test_all_mode_always_includes_a_cross_league_distractor() -> None:
    clubs = [club(number, "league-a") for number in range(1, 7)] + [club(7, "league-b")]

    result = build_choices(clubs[0], clubs, "all", rng=random.Random(3))

    scopes = [choice.scope for choice in result.choices]
    assert scopes.count("league-a") == 3
    assert scopes.count("league-b") == 1


def test_choices_have_distinct_display_names() -> None:
    clubs = [
        club(1, name="United"),
        club(2, name="United"),
        club(3),
        club(4),
        club(5),
    ]

    result = build_choices(clubs[0], clubs, "league-a", rng=random.Random(3))

    assert len({choice.name.casefold() for choice in result.choices}) == 4


def test_scope_with_too_few_distinct_club_names_cannot_build_four_choices() -> None:
    clubs = [club(1, name="United"), club(2, name="United"), club(3), club(4)]
    with pytest.raises(ChoicePoolError, match="at least four"):
        build_choices(clubs[0], clubs, "league-a")
