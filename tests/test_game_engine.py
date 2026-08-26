from __future__ import annotations

import json
import random
from collections.abc import Iterator

import pytest

from src.club_data import Club
from src.game_engine import (
    GameEngine,
    GameExpiredError,
    InvalidAnswerTokenError,
    InvalidQuestionTokenError,
    PublicGameState,
    ReusedAnswerTokenError,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def clubs() -> tuple[Club, ...]:
    return tuple(
        Club(
            id=f"private-{number}",
            provider_id=1000 + number,
            name=f"Club {number}",
            scope="league-a" if number <= 4 else "league-b",
            league="League A" if number <= 4 else "League B",
            season="2025/26",
            source_url=f"https://provider.test/{1000 + number}",
            crest_path=f"crests/crest-{number}.svg",
            covered_crest_path=f"covered-crests/covered-{number}.svg",
        )
        for number in range(1, 9)
    )


def token_factory() -> Iterator[str]:
    number = 0
    while True:
        number += 1
        yield f"opaque-token-{number}"


def engine(*, scope: str = "all", duration: int = 30) -> tuple[GameEngine, Clock]:
    clock = Clock()
    tokens = token_factory()
    game = GameEngine(
        clubs(),
        scope=scope,
        duration_seconds=duration,
        clock=clock,
        rng=random.Random(7),
        token_factory=lambda: next(tokens),
    )
    return game, clock


def answer_for_name(state: PublicGameState, name: str) -> str:
    return next(
        choice.answer_token for choice in state.question.choices if choice.name == name
    )


def target_name(game: GameEngine, state: PublicGameState) -> str:
    crest_path = game.crest_path_for(state.question.question_token)
    number = crest_path.removesuffix(".svg").rsplit("-", 1)[1]
    return f"Club {number}"


def test_public_state_has_opaque_tokens_and_no_private_identifier_fields() -> None:
    game, _clock = engine(scope="league-a", duration=60)

    state = game.state()
    wire_state = state.to_dict()
    encoded = json.dumps(wire_state)

    assert state.remaining_seconds == 60
    assert len(state.question.choices) == 4
    assert all(
        choice.answer_token.startswith("opaque-token-")
        for choice in state.question.choices
    )
    assert "provider_id" not in encoded
    assert '"id"' not in encoded
    assert "source_url" not in encoded
    assert "crest_path" not in encoded
    assert wire_state["reveal"] is None


def test_wrong_answer_is_removed_resets_streak_and_cannot_be_reused() -> None:
    game, _clock = engine(scope="league-a")
    initial = game.state()
    correct_name = target_name(game, initial)
    wrong = next(
        choice for choice in initial.question.choices if choice.name != correct_name
    )

    result = game.submit_answer(initial.question.question_token, wrong.answer_token)

    assert result.correct is False
    assert result.reveal is None
    assert len(result.state.question.choices) == 3
    assert wrong.answer_token not in {
        choice.answer_token for choice in result.state.question.choices
    }
    assert result.state.question.removed_answer_tokens == (wrong.answer_token,)
    assert result.state.points_available == 2
    with pytest.raises(ReusedAnswerTokenError):
        game.submit_answer(initial.question.question_token, wrong.answer_token)


def test_invalid_question_and_answer_tokens_are_rejected() -> None:
    game, _clock = engine()
    state = game.state()

    with pytest.raises(InvalidQuestionTokenError):
        game.submit_answer("not-issued", state.question.choices[0].answer_token)
    with pytest.raises(InvalidAnswerTokenError):
        game.submit_answer(state.question.question_token, "not-issued")


def test_crest_asset_switches_from_covered_to_original_on_resolution() -> None:
    game, clock = engine(scope="league-a")
    first = game.state()
    first_token = first.question.question_token

    assert game.crest_asset_path_for(first_token).startswith("covered-crests/")
    game.submit_answer(
        first_token,
        answer_for_name(first, target_name(game, first)),
    )
    assert game.crest_asset_path_for(first_token).startswith("crests/")

    current = game.state()
    current_token = current.question.question_token
    assert game.crest_asset_path_for(current_token).startswith("covered-crests/")
    clock.now = game.deadline
    game.state()
    assert game.crest_asset_path_for(current_token).startswith("crests/")


def test_correct_answer_reveals_then_advances_and_scores_attempt() -> None:
    game, _clock = engine(scope="league-a")
    first = game.state()
    correct_name = target_name(game, first)
    correct_token = answer_for_name(first, correct_name)

    result = game.submit_answer(first.question.question_token, correct_token)

    assert result.correct is True
    assert result.points_awarded == 3
    assert result.reveal is not None
    assert result.reveal.name == correct_name
    assert result.state.question.question_token != first.question.question_token
    assert result.state.reveal is None
    assert result.state.first_attempt_streak == 1
    with pytest.raises(InvalidQuestionTokenError, match="already been answered"):
        game.submit_answer(first.question.question_token, correct_token)


def test_deadline_is_enforced_for_each_supported_duration() -> None:
    for duration in (30, 60, 90):
        game, clock = engine(duration=duration)
        state = game.state()
        clock.now += duration

        with pytest.raises(GameExpiredError):
            game.submit_answer(
                state.question.question_token, state.question.choices[0].answer_token
            )
        expired = game.state()
        assert expired.status == "expired"
        assert expired.remaining_seconds == 0
        assert expired.reveal is not None


def test_flawless_multiplier_is_applied_only_when_game_expires() -> None:
    game, clock = engine(scope="league-a")
    for _ in range(3):
        state = game.state()
        game.submit_answer(
            state.question.question_token,
            answer_for_name(state, target_name(game, state)),
        )

    assert game.state().score == 14
    clock.now = game.deadline
    expired = game.state()
    assert expired.score == 28
    assert expired.flawless_bonus == 14


def test_invalid_duration_is_rejected() -> None:
    with pytest.raises(ValueError, match="30, 60, or 90"):
        GameEngine(clubs(), scope="all", duration_seconds=45)
