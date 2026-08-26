from __future__ import annotations

import mimetypes
from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from server.config import Settings
from server.game_service import (
    DEFAULT_MAX_ROUNDS,
    DEFAULT_ROUND_RETENTION_SECONDS,
    GameService,
    GameServiceError,
    PlayerRequiredError,
    RequestTokenError,
    RoundConflictError,
    ServiceUnavailableError,
)
from server.models import (
    SUPPORTED_DURATIONS,
    SUPPORTED_SCOPES,
    ActiveRoundResponse,
    AdvanceRequest,
    ExpiredRoundResponse,
    GuessRequest,
    GuessResponse,
    LeaderboardResponse,
    LeagueScope,
    PlayerResponse,
    PlayerUpdate,
    RoundStartRequest,
    ServiceResponse,
    StateResponse,
)


def _session_player_id(request: Request) -> str | None:
    value = request.session.get("player_id")
    return value if isinstance(value, str) and value else None


def _session_username(request: Request) -> str | None:
    value = request.session.get("username")
    return value if isinstance(value, str) and value else None


def _state_for(request: Request, service: GameService) -> StateResponse:
    username = _session_username(request)
    player_id = _session_player_id(request)
    player = PlayerResponse(username=username) if username is not None else None
    round_data = service.state_for(player_id)
    if round_data is None:
        player_round = None
    elif round_data["status"] == "active":
        player_round = ActiveRoundResponse.model_validate(round_data)
    else:
        player_round = ExpiredRoundResponse.model_validate(round_data)
    return StateResponse(
        player=player,
        supported_scopes=SUPPORTED_SCOPES,
        supported_durations=SUPPORTED_DURATIONS,
        service=ServiceResponse.model_validate(service.service_status()),
        round=player_round,
    )


def create_app(
    settings: Settings | None = None,
    *,
    serve_frontend: bool = True,
    game_service: GameService | None = None,
    clock: Callable[[], float] | None = None,
    submitted_at_clock: Callable[[], datetime] | None = None,
    token_factory: Callable[[], str] | None = None,
    round_retention_seconds: float | None = None,
    max_rounds: int | None = None,
) -> FastAPI:
    app_settings = settings or Settings.from_environment()
    service = game_service or GameService(
        app_settings.data_path,
        app_settings.leaderboard_path,
        clock=clock,
        submitted_at_clock=submitted_at_clock,
        token_factory=token_factory,
        round_retention_seconds=(
            round_retention_seconds
            if round_retention_seconds is not None
            else DEFAULT_ROUND_RETENTION_SECONDS
        ),
        max_rounds=max_rounds if max_rounds is not None else DEFAULT_MAX_ROUNDS,
    )
    app = FastAPI(
        title="Crest Quest API",
        version="0.1.0",
        docs_url="/api/docs" if app_settings.environment != "production" else None,
        redoc_url=None,
        openapi_url=(
            "/api/openapi.json" if app_settings.environment != "production" else None
        ),
    )
    app.state.settings = app_settings
    app.state.game_service = service
    app.add_middleware(
        SessionMiddleware,
        secret_key=app_settings.session_secret,
        session_cookie="crest_quest_session",
        max_age=60 * 60 * 24 * 365,
        same_site="lax",
        https_only=app_settings.secure_cookies,
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        errors = error.errors()
        detail = (
            str(errors[0].get("msg", "Invalid request."))
            if errors
            else "Invalid request."
        )
        if detail.startswith("Value error, "):
            detail = detail.removeprefix("Value error, ")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": detail},
        )

    @app.exception_handler(GameServiceError)
    async def game_service_error(
        _request: Request,
        error: GameServiceError,
    ) -> JSONResponse:
        if isinstance(error, PlayerRequiredError):
            status_code = status.HTTP_401_UNAUTHORIZED
        elif isinstance(error, RequestTokenError):
            status_code = status.HTTP_400_BAD_REQUEST
        elif isinstance(error, RoundConflictError):
            status_code = status.HTTP_409_CONFLICT
        elif isinstance(error, ServiceUnavailableError):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return JSONResponse(status_code=status_code, content={"detail": str(error)})

    @app.get("/api/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health", include_in_schema=False)
    async def health() -> JSONResponse:
        ready = service.data_ready
        return JSONResponse(
            status_code=(
                status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            content={"status": "ok" if ready else "not-ready"},
        )

    @app.get("/api/state", response_model=StateResponse)
    async def get_state(request: Request) -> StateResponse:
        return _state_for(request, service)

    @app.put("/api/player", response_model=StateResponse)
    async def update_player(
        player_update: PlayerUpdate,
        request: Request,
    ) -> StateResponse:
        player_id = _session_player_id(request) or uuid4().hex
        request.session["player_id"] = player_id
        request.session["username"] = player_update.username
        service.update_player(player_id, player_update.username)
        return _state_for(request, service)

    @app.post("/api/player/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request) -> Response:
        service.remove_round(_session_player_id(request))
        request.session.clear()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/round/start", response_model=ActiveRoundResponse)
    async def start_round(
        round_request: RoundStartRequest,
        request: Request,
    ) -> dict[str, object]:
        return service.start_round(
            _session_player_id(request),
            _session_username(request),
            round_request.scope,
            round_request.duration,
        )

    @app.post("/api/round/guess", response_model=GuessResponse)
    async def guess(
        guess_request: GuessRequest,
        request: Request,
    ) -> dict[str, object]:
        return service.guess(
            _session_player_id(request),
            guess_request.question_token,
            guess_request.answer_token,
        )

    @app.post(
        "/api/round/advance",
        response_model=ActiveRoundResponse | ExpiredRoundResponse,
    )
    async def advance(
        advance_request: AdvanceRequest,
        request: Request,
    ) -> dict[str, object]:
        return service.advance(
            _session_player_id(request), advance_request.advance_token
        )

    @app.post("/api/round/expire", response_model=ExpiredRoundResponse)
    async def expire(request: Request) -> dict[str, object]:
        return service.expire(_session_player_id(request))

    @app.get("/api/questions/{question_token}/crest")
    async def crest(question_token: str, request: Request) -> FileResponse:
        path = service.crest_path(_session_player_id(request), question_token)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/leaderboard", response_model=LeaderboardResponse)
    async def leaderboard(
        request: Request,
        scope: Annotated[LeagueScope, Query()],
        duration: Annotated[int, Query()],
    ) -> dict[str, object]:
        return service.leaderboard(
            scope, duration, player_id=_session_player_id(request)
        )

    @app.post("/api/leaderboard/retry", response_model=ExpiredRoundResponse)
    async def retry_leaderboard(request: Request) -> dict[str, object]:
        return service.retry_submission(_session_player_id(request))

    @app.post("/api/setup/retry", response_model=StateResponse)
    async def retry_setup(request: Request) -> StateResponse:
        service.reload_catalog()
        return _state_for(request, service)

    if serve_frontend and app_settings.frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=app_settings.frontend_dist, html=True),
            name="frontend",
        )
    elif serve_frontend:

        @app.get("/", include_in_schema=False)
        async def frontend_not_built() -> JSONResponse:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": (
                        "Frontend assets are not built. Run the Vite development "
                        "server or build frontend/dist."
                    )
                },
            )

    return app


app = create_app()
