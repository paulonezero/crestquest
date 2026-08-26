from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

from server.leaderboard import (
    LeaderboardEntry,
    LeaderboardError,
    SQLiteLeaderboard,
)
from server.models import (
    SUPPORTED_COMPETITIONS,
    SUPPORTED_DURATIONS,
    SUPPORTED_SCOPES,
    LeagueScope,
)
from src.club_data import (
    ClubCatalog,
    ClubDataError,
    load_club_catalog,
    load_packaged_data,
)
from src.game_engine import (
    GameEngine,
    GameExpiredError,
    InvalidAnswerTokenError,
    InvalidQuestionTokenError,
    ReusedAnswerTokenError,
)

DEFAULT_ROUND_RETENTION_SECONDS = 15 * 60
DEFAULT_MAX_ROUNDS = 10_000


class GameServiceError(RuntimeError):
    """Base error raised by the API-facing game service."""


class PlayerRequiredError(GameServiceError):
    """Raised when gameplay is requested before a player is configured."""


class RoundConflictError(GameServiceError):
    """Raised when a command does not match the player's round state."""


class RequestTokenError(GameServiceError):
    """Raised for invalid, stale, or reused public tokens."""


class ServiceUnavailableError(GameServiceError):
    """Raised when packaged data or leaderboard storage is unavailable."""


class _Leaderboard(Protocol):
    def submit(self, entry: LeaderboardEntry) -> Any: ...

    def get_top(self, scope: LeagueScope | str, duration: int) -> Any: ...


@dataclass(slots=True)
class _PlayerRound:
    player_id: str
    username: str
    scope: LeagueScope
    duration: int
    engine: GameEngine
    round_token: str
    last_accessed_at: float
    revision: int = 1
    best_streak: int = 0
    clean_three_bonuses: int = 0
    pending_reveal: dict[str, str] | None = None
    pending_advance_token: str | None = None
    used_advance_tokens: set[str] = field(default_factory=set)
    submission_status: str = "fresh"
    submission_entry: LeaderboardEntry | None = None
    made_top_10: bool = False
    expired_result: dict[str, Any] | None = None


class GameService:
    """Own in-memory rounds and adapt the domain engine for the HTTP API."""

    def __init__(
        self,
        data_path: Path,
        leaderboard_path: Path,
        *,
        clock: Callable[[], float] | None = None,
        submitted_at_clock: Callable[[], datetime] | None = None,
        leaderboard: _Leaderboard | None = None,
        token_factory: Callable[[], str] | None = None,
        round_retention_seconds: float = DEFAULT_ROUND_RETENTION_SECONDS,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
    ) -> None:
        if round_retention_seconds <= 0:
            raise ValueError("round_retention_seconds must be positive")
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int):
            raise TypeError("max_rounds must be an integer")
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")

        self._data_path = Path(data_path)
        self._leaderboard_path = Path(leaderboard_path)
        self._clock = clock or time.monotonic
        self._submitted_at_clock = submitted_at_clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._round_retention_seconds = float(round_retention_seconds)
        self._max_rounds = max_rounds
        self._lock = threading.RLock()
        self._rounds: dict[str, _PlayerRound] = {}
        self._issued_service_tokens: set[str] = set()
        self._catalog: ClubCatalog | None = None
        self._catalog_error: str | None = None
        self._leaderboard: _Leaderboard | None = leaderboard
        self._leaderboard_error: str | None = None

        self.reload_catalog()
        if leaderboard is None:
            self._initialize_leaderboard()

    @property
    def data_ready(self) -> bool:
        return self._catalog is not None

    @property
    def leaderboard_ready(self) -> bool:
        return self._leaderboard is not None and self._leaderboard_error is None

    @property
    def round_count(self) -> int:
        with self._lock:
            self._purge_abandoned()
            return len(self._rounds)

    def service_status(self) -> dict[str, Any]:
        details = [
            detail
            for detail in (self._catalog_error, self._leaderboard_error)
            if detail
        ]
        if not self.data_ready:
            status = "setup-required"
        elif not self.leaderboard_ready:
            status = "degraded"
        else:
            status = "ready"
        return {
            "status": status,
            "data_ready": self.data_ready,
            "leaderboard_ready": self.leaderboard_ready,
            "detail": " ".join(details) or None,
        }

    def reload_catalog(self) -> None:
        with self._lock:
            try:
                manifest = load_packaged_data(self._data_path, strict=False)
                if not isinstance(manifest.get("generated_at"), str):
                    raise ClubDataError("The catalog generation timestamp is missing.")
                competition_rows = manifest["competitions"]
                competitions = {
                    row.get("scope"): row
                    for row in competition_rows
                    if isinstance(row, dict) and isinstance(row.get("scope"), str)
                }
                if set(competitions) != set(SUPPORTED_COMPETITIONS):
                    raise ClubDataError(
                        "The catalog must contain exactly the seven supported "
                        "competitions."
                    )
                for scope, (
                    expected_name,
                    expected_code,
                ) in SUPPORTED_COMPETITIONS.items():
                    competition = competitions[scope]
                    if competition.get("name") != expected_name:
                        raise ClubDataError(
                            f"Competition name is invalid for {scope!r}."
                        )
                    if competition.get("code") != expected_code:
                        raise ClubDataError(
                            f"Competition code is invalid for {scope!r}."
                        )
                    if not isinstance(competition.get("season"), str):
                        raise ClubDataError(
                            f"Competition season is invalid for {scope!r}."
                        )

                catalog = load_club_catalog(self._data_path)
                for club in catalog.clubs:
                    competition = competitions.get(club.scope)
                    if competition is None:
                        raise ClubDataError(
                            f"Club scope is unsupported: {club.scope!r}."
                        )
                    if club.league != competition["name"]:
                        raise ClubDataError(
                            f"Club league is invalid for {club.scope!r}."
                        )
                    if club.season != competition["season"]:
                        raise ClubDataError(
                            f"Club season is invalid for {club.scope!r}."
                        )
                for scope in SUPPORTED_SCOPES[1:]:
                    distinct_names = {
                        club.name.casefold()
                        for club in catalog.clubs
                        if club.scope == scope
                    }
                    if len(distinct_names) < 4:
                        raise ClubDataError(
                            f"Scope {scope!r} needs at least four distinct club names."
                        )
            except (ClubDataError, OSError):
                self._catalog = None
                self._catalog_error = (
                    "Club data is not ready: packaged catalog validation failed. "
                    f"Check {self._data_path} and retry setup."
                )
                return
            self._catalog = catalog
            self._catalog_error = None

    def update_player(self, player_id: str, username: str) -> None:
        with self._lock:
            self._purge_abandoned()
            player_round = self._rounds.get(player_id)
            if player_round is not None:
                player_round.last_accessed_at = self._now()
                player_round.username = username

    def remove_round(self, player_id: str | None) -> None:
        if player_id is None:
            return
        with self._lock:
            self._rounds.pop(player_id, None)

    def state_for(self, player_id: str | None) -> dict[str, Any] | None:
        if player_id is None:
            return None
        with self._lock:
            self._purge_abandoned()
            player_round = self._rounds.get(player_id)
            if player_round is None:
                return None
            player_round.last_accessed_at = self._now()
            return self._state_for_round(player_round)

    def start_round(
        self,
        player_id: str | None,
        username: str | None,
        scope: str,
        duration: int,
    ) -> dict[str, Any]:
        if not player_id or not username:
            raise PlayerRequiredError("Set a player name before starting a round.")
        if scope not in SUPPORTED_SCOPES:
            raise RequestTokenError(f"Unsupported game scope: {scope!r}.")
        if isinstance(duration, bool) or duration not in SUPPORTED_DURATIONS:
            raise RequestTokenError(f"Unsupported game duration: {duration!r}.")

        with self._lock:
            self._purge_abandoned()
            if self._catalog is None:
                raise ServiceUnavailableError(
                    self._catalog_error or "Club data is not ready. Retry setup."
                )
            previous = self._rounds.get(player_id)
            if previous is not None:
                previous.last_accessed_at = self._now()
                previous_state = self._state_for_round(previous)
                if previous_state["status"] == "active":
                    raise RoundConflictError(
                        "A round is already active for this player."
                    )
                if previous.submission_status == "pending":
                    raise RoundConflictError(
                        "Retry the pending leaderboard submission before "
                        "starting again."
                    )
            try:
                engine = GameEngine(
                    self._catalog,
                    scope=scope,
                    duration_seconds=duration,
                    clock=self._clock,
                )
            except (ValueError, RuntimeError) as error:
                raise ServiceUnavailableError(
                    f"A game could not be started for {scope!r}: {error}"
                ) from error
            if previous is None:
                self._evict_for_capacity()
            player_round = _PlayerRound(
                player_id=player_id,
                username=username,
                scope=cast(LeagueScope, scope),
                duration=duration,
                engine=engine,
                round_token=self._new_service_token(),
                last_accessed_at=self._now(),
            )
            self._rounds[player_id] = player_round
            return self._active_state(player_round, engine.state().to_dict())

    def guess(
        self,
        player_id: str | None,
        question_token: str,
        answer_token: str,
    ) -> dict[str, Any]:
        with self._lock:
            player_round = self._required_round(player_id)
            if player_round.expired_result is not None or player_round.engine.expired:
                self._finalize(player_round)
                raise RoundConflictError(
                    "The round deadline has passed; request the expiry result."
                )
            if player_round.pending_reveal is not None:
                raise RoundConflictError(
                    "Advance after the correct answer before submitting another guess."
                )
            answered_crest_url = f"/api/questions/{question_token}/crest"
            try:
                result = player_round.engine.submit_answer(question_token, answer_token)
            except ReusedAnswerTokenError as error:
                raise RequestTokenError(
                    "That answer token has already been used."
                ) from error
            except InvalidQuestionTokenError as error:
                raise RequestTokenError(str(error)) from error
            except InvalidAnswerTokenError as error:
                raise RequestTokenError(str(error)) from error
            except GameExpiredError as error:
                self._finalize(player_round)
                raise RoundConflictError(
                    "The round deadline has passed; request the expiry result."
                ) from error

            result_data = result.to_dict()
            player_round.revision += 1
            player_round.best_streak = max(
                player_round.best_streak,
                int(result_data["state"]["first_attempt_streak"]),
            )
            if int(result_data["bonus_points"]) > 0:
                player_round.clean_three_bonuses += 1
            if result.correct:
                reveal = cast(dict[str, str], result_data["reveal"])
                reveal["crest_url"] = answered_crest_url
                player_round.pending_reveal = reveal
                result_data["reveal"] = reveal
                player_round.pending_advance_token = self._new_service_token()
            return {
                "correct": bool(result_data["correct"]),
                "points_awarded": int(result_data["points_awarded"]),
                "base_points": int(result_data["base_points"]),
                "bonus_points": int(result_data["bonus_points"]),
                "reveal": result_data["reveal"],
                "state": self._active_state(player_round, result_data["state"]),
            }

    def advance(self, player_id: str | None, advance_token: str) -> dict[str, Any]:
        with self._lock:
            player_round = self._required_round(player_id)
            if advance_token in player_round.used_advance_tokens:
                raise RequestTokenError("That advance token has already been used.")
            pending_token = player_round.pending_advance_token
            if pending_token is None or advance_token != pending_token:
                raise RequestTokenError("The advance token is invalid or stale.")
            if player_round.engine.expired:
                return self._finalize(player_round)

            player_round.used_advance_tokens.add(advance_token)
            player_round.pending_reveal = None
            player_round.pending_advance_token = None
            player_round.revision += 1
            return self._active_state(
                player_round, player_round.engine.state().to_dict()
            )

    def expire(self, player_id: str | None) -> dict[str, Any]:
        with self._lock:
            player_round = self._required_round(player_id)
            if not player_round.engine.expired:
                raise RoundConflictError("The round is still active.")
            return self._finalize(player_round)

    def retry_submission(self, player_id: str | None) -> dict[str, Any]:
        with self._lock:
            player_round = self._required_round(player_id)
            if not player_round.engine.expired:
                raise RoundConflictError("Only an expired round can be submitted.")
            self._finalize(player_round)
            if player_round.submission_status != "pending":
                raise RoundConflictError(
                    "There is no pending leaderboard submission to retry."
                )
            self._submit(player_round)
            return self._expired_result(player_round)

    def crest_path(self, player_id: str | None, question_token: str) -> Path:
        with self._lock:
            player_round = self._required_round(player_id)
            try:
                relative_path = player_round.engine.crest_asset_path_for(question_token)
            except InvalidQuestionTokenError as error:
                raise RequestTokenError("The question token is invalid.") from error
            pure_path = PurePosixPath(relative_path)
            asset_path = (self._data_path.parent / Path(*pure_path.parts)).resolve()
            data_root = self._data_path.parent.resolve()
            if not asset_path.is_relative_to(data_root) or not asset_path.is_file():
                raise ServiceUnavailableError(
                    "The requested crest asset is unavailable."
                )
            return asset_path

    def leaderboard(
        self,
        scope: str,
        duration: int,
        *,
        player_id: str | None = None,
    ) -> dict[str, Any]:
        if scope not in SUPPORTED_SCOPES:
            raise RequestTokenError(f"Unsupported leaderboard scope: {scope!r}.")
        if isinstance(duration, bool) or duration not in SUPPORTED_DURATIONS:
            raise RequestTokenError(f"Unsupported leaderboard duration: {duration!r}.")
        with self._lock:
            self._purge_abandoned()
            try:
                board = self._require_leaderboard()
                entries = board.get_top(scope, duration)
            except (LeaderboardError, OSError, ServiceUnavailableError) as error:
                self._leaderboard_error = f"Leaderboard unavailable: {error}"
                raise ServiceUnavailableError(self._leaderboard_error) from error
            self._leaderboard_error = None
            return {
                "scope": scope,
                "duration": duration,
                "entries": [
                    {
                        "rank": rank,
                        "username": entry.username,
                        "is_current_player": bool(
                            player_id and entry.player_id == player_id
                        ),
                        "score": entry.score,
                        "clubs_named": entry.clubs_named,
                        "incorrect_selections": entry.incorrect_selections,
                        "best_streak": entry.best_streak,
                        "clean_three_bonuses": entry.clean_three_bonuses,
                        "flawless_multiplier": entry.flawless_multiplier,
                        "submitted_at": entry.submitted_at.isoformat(),
                    }
                    for rank, entry in enumerate(entries, start=1)
                ],
            }

    def _required_round(self, player_id: str | None) -> _PlayerRound:
        if not player_id:
            raise PlayerRequiredError("Set a player name before playing.")
        self._purge_abandoned()
        try:
            player_round = self._rounds[player_id]
        except KeyError as error:
            raise RoundConflictError("No round exists for this player.") from error
        player_round.last_accessed_at = self._now()
        return player_round

    def _state_for_round(self, player_round: _PlayerRound) -> dict[str, Any]:
        if player_round.expired_result is not None:
            return self._expired_result(player_round)
        state = player_round.engine.state().to_dict()
        player_round.best_streak = max(
            player_round.best_streak, int(state["first_attempt_streak"])
        )
        if state["status"] == "expired":
            return self._finalize(player_round, state)
        return self._active_state(player_round, state)

    def _active_state(
        self, player_round: _PlayerRound, state: dict[str, Any]
    ) -> dict[str, Any]:
        question = dict(state["question"])
        question["crest_url"] = f"/api/questions/{question['question_token']}/crest"
        active_state = {
            **state,
            "status": "active",
            "round_token": player_round.round_token,
            "revision": player_round.revision,
            "best_streak": player_round.best_streak,
            "clean_three_bonuses": player_round.clean_three_bonuses,
            "awaiting_advance": player_round.pending_reveal is not None,
            "question": question,
            "reveal": player_round.pending_reveal,
        }
        if player_round.pending_advance_token is not None:
            active_state["advance_token"] = player_round.pending_advance_token
        return active_state

    def _finalize(
        self, player_round: _PlayerRound, state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if player_round.expired_result is None:
            state = state or player_round.engine.state().to_dict()
            if state["status"] != "expired":
                raise RoundConflictError("The round is still active.")
            player_round.best_streak = max(
                player_round.best_streak, int(state["first_attempt_streak"])
            )
            reveal = cast(dict[str, str] | None, state["reveal"])
            if reveal is None:
                raise RuntimeError("Expired domain state did not reveal the final club")
            question = cast(dict[str, Any], state["question"])
            reveal["crest_url"] = f"/api/questions/{question['question_token']}/crest"
            if player_round.pending_advance_token is not None:
                player_round.used_advance_tokens.add(player_round.pending_advance_token)
            player_round.pending_reveal = None
            player_round.pending_advance_token = None
            player_round.revision += 1
            player_round.expired_result = {
                "status": "expired",
                "round_token": player_round.round_token,
                "revision": player_round.revision,
                "scope": player_round.scope,
                "duration_seconds": player_round.duration,
                "final_score": int(state["score"]),
                "clubs_named": int(state["correct_answers"]),
                "incorrect_selections": int(state["incorrect_selections"]),
                "best_streak": player_round.best_streak,
                "clean_three_bonuses": player_round.clean_three_bonuses,
                "flawless_multiplier": 2 if int(state["flawless_bonus"]) > 0 else 1,
                "final_unanswered_club": reveal,
            }
            if int(state["score"]) <= 0:
                player_round.submission_status = "not-qualified"
            else:
                submitted_at = self._submitted_at_clock()
                player_round.submission_entry = LeaderboardEntry(
                    round_id=player_round.round_token,
                    player_id=player_round.player_id,
                    username=player_round.username,
                    score=int(state["score"]),
                    clubs_named=int(state["correct_answers"]),
                    incorrect_selections=int(state["incorrect_selections"]),
                    best_streak=player_round.best_streak,
                    clean_three_bonuses=player_round.clean_three_bonuses,
                    flawless_multiplier=(
                        2.0 if int(state["flawless_bonus"]) > 0 else 1.0
                    ),
                    scope=player_round.scope,
                    duration=player_round.duration,
                    submitted_at=submitted_at,
                )
                self._submit(player_round)
        return self._expired_result(player_round)

    def _submit(self, player_round: _PlayerRound) -> None:
        if player_round.submission_status == "submitted":
            return
        submission_entry = player_round.submission_entry
        if submission_entry is None:
            raise RuntimeError("A qualifying round has no captured submission")
        try:
            board = self._require_leaderboard()
            submission = board.submit(submission_entry)
        except (LeaderboardError, OSError, ServiceUnavailableError) as error:
            player_round.submission_status = "pending"
            self._leaderboard_error = f"Leaderboard submission failed: {error}"
            return
        player_round.submission_status = "submitted"
        player_round.made_top_10 = bool(submission.reached_top_10)
        self._leaderboard_error = None

    def _expired_result(self, player_round: _PlayerRound) -> dict[str, Any]:
        if player_round.expired_result is None:
            raise RoundConflictError("The round has not expired.")
        return {
            **player_round.expired_result,
            "leaderboard_submission_pending": (
                player_round.submission_status == "pending"
            ),
            "made_top_10": player_round.made_top_10,
        }

    def _purge_abandoned(self) -> None:
        if not self._rounds:
            return
        now = self._now()
        abandoned = [
            player_id
            for player_id, player_round in self._rounds.items()
            if now - player_round.last_accessed_at >= self._round_retention_seconds
        ]
        for player_id in abandoned:
            self._rounds.pop(player_id, None)

    def _evict_for_capacity(self) -> None:
        while len(self._rounds) >= self._max_rounds:
            player_id, _ = min(
                self._rounds.items(),
                key=lambda item: (
                    item[1].submission_status == "pending",
                    item[1].expired_result is None,
                    item[1].last_accessed_at,
                ),
            )
            self._rounds.pop(player_id, None)

    def _new_service_token(self) -> str:
        for _ in range(100):
            token = self._token_factory()
            if (
                isinstance(token, str)
                and token
                and token not in self._issued_service_tokens
            ):
                self._issued_service_tokens.add(token)
                return token
        raise RuntimeError("Could not generate a unique opaque service token")

    def _now(self) -> float:
        return float(self._clock())

    def _initialize_leaderboard(self) -> None:
        try:
            self._leaderboard = SQLiteLeaderboard(self._leaderboard_path)
            self._leaderboard_error = None
        except (LeaderboardError, OSError) as error:
            self._leaderboard = None
            self._leaderboard_error = (
                f"Leaderboard storage is unavailable: {error} Check "
                f"{self._leaderboard_path} and retry."
            )

    def _require_leaderboard(self) -> _Leaderboard:
        if self._leaderboard is None:
            self._initialize_leaderboard()
        if self._leaderboard is None:
            raise ServiceUnavailableError(
                self._leaderboard_error or "Leaderboard storage is unavailable."
            )
        return self._leaderboard
