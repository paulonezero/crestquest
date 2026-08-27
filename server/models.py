from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LeagueScope = Literal[
    "all",
    "premier-league",
    "bundesliga",
    "la-liga",
    "primeira-liga",
    "ligue-1",
    "serie-a",
    "eredivisie",
    "championship",
]

SUPPORTED_SCOPES: tuple[LeagueScope, ...] = (
    "all",
    "premier-league",
    "bundesliga",
    "la-liga",
    "primeira-liga",
    "ligue-1",
    "serie-a",
    "eredivisie",
    "championship",
)
SUPPORTED_DURATIONS: tuple[int, ...] = (30, 60, 90)
SUPPORTED_COMPETITIONS: dict[str, tuple[str, str]] = {
    "premier-league": ("Premier League", "PL"),
    "bundesliga": ("Bundesliga", "BL1"),
    "la-liga": ("La Liga", "PD"),
    "primeira-liga": ("Primeira Liga", "PPL"),
    "ligue-1": ("Ligue 1", "FL1"),
    "serie-a": ("Serie A", "SA"),
    "eredivisie": ("Eredivisie", "DED"),
    "championship": ("Championship", "ELC"),
}
Duration = Literal[30, 60, 90]


class PlayerUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, username: str) -> str:
        normalized = " ".join(username.split())
        if not normalized:
            raise ValueError("Enter a player name.")
        if len(normalized) > 24:
            raise ValueError("Player names must be 24 characters or fewer.")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Player names cannot contain control characters.")
        return normalized


class PlayerResponse(BaseModel):
    username: str


class ServiceResponse(BaseModel):
    status: Literal["ready", "setup-required", "degraded"]
    data_ready: bool
    leaderboard_ready: bool
    detail: str | None = None


class ChoiceResponse(BaseModel):
    answer_token: str
    name: str
    league: str


class RevealResponse(BaseModel):
    answer_token: str
    name: str
    crest_url: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class QuestionResponse(BaseModel):
    question_token: str
    crest_url: str
    choices: list[ChoiceResponse]
    removed_answer_tokens: list[str]
    round_number: int


class ActiveRoundResponse(BaseModel):
    status: Literal["active"]
    round_token: str
    revision: int = Field(ge=1)
    scope: LeagueScope
    duration_seconds: Duration
    remaining_seconds: int
    deadline: float
    score: int
    points_available: int
    first_attempt_streak: int
    best_streak: int
    clean_three_progress: int
    clean_three_bonuses: int
    correct_answers: int
    incorrect_selections: int
    flawless_bonus: int
    awaiting_advance: bool
    advance_token: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    question: QuestionResponse
    reveal: RevealResponse | None


class ExpiredRoundResponse(BaseModel):
    status: Literal["expired"]
    round_token: str
    revision: int = Field(ge=1)
    scope: LeagueScope
    duration_seconds: Duration
    final_score: int
    clubs_named: int
    incorrect_selections: int
    best_streak: int
    clean_three_bonuses: int
    flawless_multiplier: Literal[1, 2]
    final_unanswered_club: RevealResponse
    leaderboard_submission_pending: bool
    made_top_10: bool


RoundResponse = Annotated[
    ActiveRoundResponse | ExpiredRoundResponse,
    Field(discriminator="status"),
]


class StateResponse(BaseModel):
    player: PlayerResponse | None
    supported_scopes: tuple[LeagueScope, ...]
    supported_durations: tuple[int, ...]
    service: ServiceResponse
    round: RoundResponse | None


class RoundStartRequest(BaseModel):
    scope: LeagueScope
    duration: Duration


class GuessRequest(BaseModel):
    question_token: str = Field(min_length=1, max_length=256)
    answer_token: str = Field(min_length=1, max_length=256)


class AdvanceRequest(BaseModel):
    advance_token: str = Field(min_length=1, max_length=256)


class GuessResponse(BaseModel):
    correct: bool
    points_awarded: int
    base_points: int
    bonus_points: int
    reveal: RevealResponse | None
    state: ActiveRoundResponse


class LeaderboardRowResponse(BaseModel):
    rank: int
    username: str
    is_current_player: bool
    score: int
    clubs_named: int
    incorrect_selections: int
    best_streak: int
    clean_three_bonuses: int
    flawless_multiplier: float
    submitted_at: str


class LeaderboardResponse(BaseModel):
    scope: LeagueScope
    duration: Duration
    entries: list[LeaderboardRowResponse]
