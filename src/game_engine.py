from __future__ import annotations

import math
import random
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from src.club_data import Club, ClubCatalog
from src.distractors import ChoicePoolError, Randomizer, TargetPool, build_choices
from src.scoring import ScoreEvent, ScoreTracker, points_for_attempt

SUPPORTED_DURATIONS = frozenset({30, 60, 90})


class GameEngineError(RuntimeError):
    """Base class for game-engine command errors."""


class GameExpiredError(GameEngineError):
    """Raised when an answer arrives at or after the deadline."""


class InvalidQuestionTokenError(GameEngineError):
    """Raised when a question token is unknown or no longer current."""


class InvalidAnswerTokenError(GameEngineError):
    """Raised when an answer token was not issued for the current question."""


class ReusedAnswerTokenError(InvalidAnswerTokenError):
    """Raised when a wrong answer token is submitted more than once."""


@dataclass(frozen=True, slots=True)
class PublicChoice:
    answer_token: str
    name: str
    league: str

    def to_dict(self) -> dict[str, str]:
        return {
            "answer_token": self.answer_token,
            "name": self.name,
            "league": self.league,
        }


@dataclass(frozen=True, slots=True)
class PublicQuestion:
    question_token: str
    choices: tuple[PublicChoice, ...]
    removed_answer_tokens: tuple[str, ...]
    round_number: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_token": self.question_token,
            "choices": [choice.to_dict() for choice in self.choices],
            "removed_answer_tokens": list(self.removed_answer_tokens),
            "round_number": self.round_number,
        }


@dataclass(frozen=True, slots=True)
class CorrectReveal:
    answer_token: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"answer_token": self.answer_token, "name": self.name}


@dataclass(frozen=True, slots=True)
class PublicGameState:
    status: str
    scope: str
    duration_seconds: int
    remaining_seconds: int
    deadline: float
    score: int
    points_available: int
    first_attempt_streak: int
    clean_three_progress: int
    correct_answers: int
    incorrect_selections: int
    flawless_bonus: int
    question: PublicQuestion
    reveal: CorrectReveal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "scope": self.scope,
            "duration_seconds": self.duration_seconds,
            "remaining_seconds": self.remaining_seconds,
            "deadline": self.deadline,
            "score": self.score,
            "points_available": self.points_available,
            "first_attempt_streak": self.first_attempt_streak,
            "clean_three_progress": self.clean_three_progress,
            "correct_answers": self.correct_answers,
            "incorrect_selections": self.incorrect_selections,
            "flawless_bonus": self.flawless_bonus,
            "question": self.question.to_dict(),
            "reveal": self.reveal.to_dict() if self.reveal else None,
        }


@dataclass(frozen=True, slots=True)
class AnswerResult:
    correct: bool
    points_awarded: int
    base_points: int
    bonus_points: int
    reveal: CorrectReveal | None
    state: PublicGameState

    def to_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "points_awarded": self.points_awarded,
            "base_points": self.base_points,
            "bonus_points": self.bonus_points,
            "reveal": self.reveal.to_dict() if self.reveal else None,
            "state": self.state.to_dict(),
        }


@dataclass(slots=True)
class _Round:
    number: int
    question_token: str
    target: Club
    answer_tokens: dict[str, Club]
    choice_order: tuple[str, ...]
    used_answer_tokens: set[str]

    @property
    def correct_token(self) -> str:
        for token, club in self.answer_tokens.items():
            if club.id == self.target.id:
                return token
        raise AssertionError("Round target has no answer token")


class GameEngine:
    """A framework-independent, server-authoritative timed game.

    The engine starts immediately. The caller stores this object in its own session
    layer and sends only ``PublicGameState.to_dict()`` or ``AnswerResult.to_dict()``
    over the wire.
    """

    def __init__(
        self,
        clubs: ClubCatalog | Iterable[Club],
        *,
        scope: str,
        duration_seconds: int,
        clock: Callable[[], float] | None = None,
        rng: Randomizer | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if (
            isinstance(duration_seconds, bool)
            or duration_seconds not in SUPPORTED_DURATIONS
        ):
            raise ValueError("duration_seconds must be one of 30, 60, or 90")

        self._clubs = clubs.clubs if isinstance(clubs, ClubCatalog) else tuple(clubs)
        self._validate_scope(scope)
        self._scope = scope
        self._duration_seconds = duration_seconds
        self._clock = clock or time.monotonic
        self._rng = rng or random.Random()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(18))
        self._issued_tokens: set[str] = set()
        self._past_question_tokens: set[str] = set()
        self._crest_assets_by_question: dict[str, tuple[str, str]] = {}
        self._target_pool = TargetPool(self._clubs, rng=self._rng)
        self._scoring = ScoreTracker()
        self._expired = False
        self._round_number = 0

        self._started_at = float(self._clock())
        self._deadline = self._started_at + duration_seconds
        self._round = self._new_round()

    @property
    def deadline(self) -> float:
        return self._deadline

    @property
    def expired(self) -> bool:
        self._expire_if_needed(float(self._clock()))
        return self._expired

    def state(self) -> PublicGameState:
        now = float(self._clock())
        self._expire_if_needed(now)
        return self._public_state(now)

    get_state = state

    def crest_path_for(self, question_token: str) -> str:
        """Resolve an opaque question token for a trusted server crest endpoint.

        The returned packaged path must not be included in public play-state data.
        """

        try:
            original_path, _covered_path = self._crest_assets_by_question[
                question_token
            ]
            return original_path
        except KeyError as error:
            raise InvalidQuestionTokenError("The question token is invalid") from error

    def crest_asset_path_for(self, question_token: str) -> str:
        """Return the covered asset until this server-side question is revealed."""
        try:
            original_path, covered_path = self._crest_assets_by_question[question_token]
        except KeyError as error:
            raise InvalidQuestionTokenError("The question token is invalid") from error
        return (
            original_path if self.question_is_revealed(question_token) else covered_path
        )

    def question_is_revealed(self, question_token: str) -> bool:
        if question_token not in self._crest_assets_by_question:
            raise InvalidQuestionTokenError("The question token is invalid")
        return question_token in self._past_question_tokens or (
            self._expired and question_token == self._round.question_token
        )

    def submit_answer(self, question_token: str, answer_token: str) -> AnswerResult:
        now = float(self._clock())
        self._expire_if_needed(now)
        if self._expired:
            raise GameExpiredError("The game deadline has passed")
        if question_token != self._round.question_token:
            if question_token in self._past_question_tokens:
                raise InvalidQuestionTokenError(
                    "That question has already been answered"
                )
            raise InvalidQuestionTokenError("The question token is invalid")
        if answer_token in self._round.used_answer_tokens:
            raise ReusedAnswerTokenError("That answer has already been used")
        if answer_token not in self._round.answer_tokens:
            raise InvalidAnswerTokenError("The answer token is invalid")

        selected = self._round.answer_tokens[answer_token]
        if selected.id != self._round.target.id:
            self._round.used_answer_tokens.add(answer_token)
            self._scoring.record_wrong()
            return AnswerResult(
                correct=False,
                points_awarded=0,
                base_points=0,
                bonus_points=0,
                reveal=None,
                state=self._public_state(now),
            )

        event = self._scoring.record_correct()
        reveal = self._correct_reveal(self._round)
        self._past_question_tokens.add(self._round.question_token)
        self._round = self._new_round()
        return self._correct_result(event, reveal, now)

    answer = submit_answer

    def _new_round(self) -> _Round:
        self._round_number += 1
        target = self._target_pool.next_target(self._scope)
        choice_set = build_choices(target, self._clubs, self._scope, rng=self._rng)
        question_token = self._new_token()
        answer_tokens: dict[str, Club] = {}
        choice_order: list[str] = []
        for club in choice_set.choices:
            token = self._new_token()
            answer_tokens[token] = club
            choice_order.append(token)
        self._crest_assets_by_question[question_token] = (
            target.crest_path,
            target.covered_crest_path,
        )
        return _Round(
            number=self._round_number,
            question_token=question_token,
            target=target,
            answer_tokens=answer_tokens,
            choice_order=tuple(choice_order),
            used_answer_tokens=set(),
        )

    def _new_token(self) -> str:
        for _ in range(100):
            token = self._token_factory()
            if isinstance(token, str) and token and token not in self._issued_tokens:
                self._issued_tokens.add(token)
                return token
        raise GameEngineError("Could not generate a unique opaque token")

    def _public_state(self, now: float) -> PublicGameState:
        summary = self._scoring.summary()
        choices = tuple(
            PublicChoice(
                token,
                self._round.answer_tokens[token].name,
                self._round.answer_tokens[token].league,
            )
            for token in self._round.choice_order
            if token not in self._round.used_answer_tokens
        )
        question = PublicQuestion(
            question_token=self._round.question_token,
            choices=choices,
            removed_answer_tokens=tuple(
                token
                for token in self._round.choice_order
                if token in self._round.used_answer_tokens
            ),
            round_number=self._round.number,
        )
        return PublicGameState(
            status="expired" if self._expired else "active",
            scope=self._scope,
            duration_seconds=self._duration_seconds,
            remaining_seconds=(
                0 if self._expired else max(0, math.ceil(self._deadline - now))
            ),
            deadline=self._deadline,
            score=summary.score,
            points_available=(
                0
                if self._expired
                else points_for_attempt(len(self._round.used_answer_tokens) + 1)
            ),
            first_attempt_streak=summary.first_attempt_streak,
            clean_three_progress=summary.clean_three_progress,
            correct_answers=summary.correct_answers,
            incorrect_selections=summary.incorrect_selections,
            flawless_bonus=summary.flawless_bonus,
            question=question,
            reveal=self._correct_reveal(self._round) if self._expired else None,
        )

    def _correct_reveal(self, game_round: _Round) -> CorrectReveal:
        return CorrectReveal(
            answer_token=game_round.correct_token,
            name=game_round.target.name,
        )

    def _correct_result(
        self, event: ScoreEvent, reveal: CorrectReveal, now: float
    ) -> AnswerResult:
        return AnswerResult(
            correct=True,
            points_awarded=event.points,
            base_points=event.base_points,
            bonus_points=event.bonus_points,
            reveal=reveal,
            state=self._public_state(now),
        )

    def _expire_if_needed(self, now: float) -> None:
        if not self._expired and now >= self._deadline:
            self._scoring.expire()
            self._expired = True

    def _validate_scope(self, scope: str) -> None:
        eligible = (
            self._clubs
            if scope == "all"
            else tuple(club for club in self._clubs if club.scope == scope)
        )
        if len(eligible) < 4:
            raise ChoicePoolError(
                f"Scope {scope!r} needs at least four eligible clubs for a game"
            )
