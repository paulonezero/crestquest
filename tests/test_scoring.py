from __future__ import annotations

import pytest

from src.scoring import ScoreTracker, ScoringClosedError, points_for_attempt


def test_attempts_score_three_two_one_zero() -> None:
    assert [points_for_attempt(attempt) for attempt in range(1, 6)] == [3, 2, 1, 0, 0]


def test_first_attempt_streak_and_clean_three_bonus() -> None:
    scoring = ScoreTracker()

    first = scoring.record_correct()
    second = scoring.record_correct()
    third = scoring.record_correct()

    assert (first.points, second.points, third.points) == (3, 3, 8)
    assert scoring.score == 14
    assert scoring.first_attempt_streak == 3
    assert scoring.clean_three_progress == 0

    scoring.record_correct()
    scoring.record_correct()
    sixth = scoring.record_correct()
    assert sixth.bonus_points == 5
    assert scoring.score == 28


def test_wrong_selection_resets_both_streaks_and_reduces_round_score() -> None:
    scoring = ScoreTracker()
    scoring.record_correct()
    scoring.record_correct()

    wrong = scoring.record_wrong()
    correct = scoring.record_correct()

    assert wrong.first_attempt_streak == 0
    assert wrong.clean_three_progress == 0
    assert correct.base_points == 2
    assert scoring.first_attempt_streak == 0
    assert scoring.incorrect_selections == 1


def test_multiple_wrong_selections_produce_lower_attempt_points() -> None:
    scoring = ScoreTracker()
    scoring.record_wrong()
    scoring.record_wrong()
    assert scoring.record_correct().base_points == 1

    scoring.record_wrong()
    scoring.record_wrong()
    scoring.record_wrong()
    assert scoring.record_correct().base_points == 0


def test_flawless_multiplier_is_one_time_and_requires_three_correct() -> None:
    scoring = ScoreTracker()
    for _ in range(3):
        scoring.record_correct()

    summary = scoring.expire()
    assert summary.score == 28
    assert summary.flawless_bonus == 14
    assert scoring.expire() == summary


def test_explicit_later_attempt_accounts_for_implicit_wrong_selections() -> None:
    scoring = ScoreTracker()

    event = scoring.record_correct(attempt_number=4)

    assert event.base_points == 0
    assert scoring.incorrect_selections == 3
    assert scoring.expire().flawless_bonus == 0


def test_flawless_multiplier_is_lost_after_any_incorrect_selection() -> None:
    scoring = ScoreTracker()
    scoring.record_wrong()
    scoring.record_correct()
    for _ in range(3):
        scoring.record_correct()

    assert scoring.expire().flawless_bonus == 0
    with pytest.raises(ScoringClosedError):
        scoring.record_correct()
