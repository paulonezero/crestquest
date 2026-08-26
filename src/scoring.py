from __future__ import annotations

from dataclasses import dataclass

ATTEMPT_POINTS = (3, 2, 1)
CLEAN_THREE_BONUS = 5


class ScoringClosedError(RuntimeError):
    """Raised when a score is changed after the game has expired."""


@dataclass(frozen=True, slots=True)
class ScoreEvent:
    points: int
    base_points: int
    bonus_points: int
    score: int
    first_attempt_streak: int
    clean_three_progress: int


@dataclass(frozen=True, slots=True)
class ScoreSummary:
    score: int
    correct_answers: int
    incorrect_selections: int
    first_attempt_streak: int
    clean_three_progress: int
    flawless_bonus: int


class ScoreTracker:
    """Owns all score and streak rules for one timed game."""

    def __init__(self) -> None:
        self._score = 0
        self._correct_answers = 0
        self._incorrect_selections = 0
        self._first_attempt_streak = 0
        self._clean_three_progress = 0
        self._wrong_in_current_round = 0
        self._flawless_bonus = 0
        self._closed = False

    @property
    def score(self) -> int:
        return self._score

    @property
    def correct_answers(self) -> int:
        return self._correct_answers

    @property
    def incorrect_selections(self) -> int:
        return self._incorrect_selections

    @property
    def first_attempt_streak(self) -> int:
        return self._first_attempt_streak

    @property
    def clean_three_progress(self) -> int:
        return self._clean_three_progress

    @property
    def flawless_bonus(self) -> int:
        return self._flawless_bonus

    @property
    def closed(self) -> bool:
        return self._closed

    def record_wrong(self) -> ScoreEvent:
        self._ensure_open()
        self._incorrect_selections += 1
        self._wrong_in_current_round += 1
        self._first_attempt_streak = 0
        self._clean_three_progress = 0
        return self._event(base_points=0, bonus_points=0)

    record_incorrect = record_wrong

    def record_correct(self, attempt_number: int | None = None) -> ScoreEvent:
        self._ensure_open()
        if attempt_number is None:
            attempt_number = self._wrong_in_current_round + 1
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")

        observed_attempt = self._wrong_in_current_round + 1
        if attempt_number < observed_attempt:
            raise ValueError(
                "attempt_number conflicts with wrong selections already recorded"
            )
        for _ in range(attempt_number - observed_attempt):
            self.record_wrong()

        base_points = points_for_attempt(attempt_number)
        bonus_points = 0
        self._correct_answers += 1
        if attempt_number == 1:
            self._first_attempt_streak += 1
            self._clean_three_progress += 1
            if self._clean_three_progress == 3:
                bonus_points = CLEAN_THREE_BONUS
                self._clean_three_progress = 0
        else:
            # A wrong selection normally reset these as it happened. Keeping this
            # reset here also makes explicit-attempt use safe and deterministic.
            self._first_attempt_streak = 0
            self._clean_three_progress = 0

        self._score += base_points + bonus_points
        self._wrong_in_current_round = 0
        return self._event(base_points=base_points, bonus_points=bonus_points)

    def expire(self) -> ScoreSummary:
        """Close scoring and apply the one-time flawless multiplier."""

        if not self._closed:
            if self._correct_answers >= 3 and self._incorrect_selections == 0:
                self._flawless_bonus = self._score
                self._score *= 2
            self._closed = True
        return self.summary()

    def summary(self) -> ScoreSummary:
        return ScoreSummary(
            score=self._score,
            correct_answers=self._correct_answers,
            incorrect_selections=self._incorrect_selections,
            first_attempt_streak=self._first_attempt_streak,
            clean_three_progress=self._clean_three_progress,
            flawless_bonus=self._flawless_bonus,
        )

    def _event(self, *, base_points: int, bonus_points: int) -> ScoreEvent:
        return ScoreEvent(
            points=base_points + bonus_points,
            base_points=base_points,
            bonus_points=bonus_points,
            score=self._score,
            first_attempt_streak=self._first_attempt_streak,
            clean_three_progress=self._clean_three_progress,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise ScoringClosedError("Scoring is closed because the game has expired")


def points_for_attempt(attempt_number: int) -> int:
    """Return 3/2/1/0 points for first/subsequent attempts."""

    if isinstance(attempt_number, bool) or not isinstance(attempt_number, int):
        raise TypeError("attempt_number must be an integer")
    if attempt_number < 1:
        raise ValueError("attempt_number must be at least 1")
    try:
        return ATTEMPT_POINTS[attempt_number - 1]
    except IndexError:
        return 0


Scorer = ScoreTracker
