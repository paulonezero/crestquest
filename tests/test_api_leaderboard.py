from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.game_service import GameService, ServiceUnavailableError
from server.leaderboard import (
    LeaderboardEntry,
    LeaderboardReadError,
    LeaderboardWriteError,
    SubmissionResult,
)
from server.models import SUPPORTED_COMPETITIONS, SUPPORTED_SCOPES, LeagueScope

_png_output = io.BytesIO()
Image.new("RGBA", (256, 256), (20, 80, 160, 0)).save(_png_output, "PNG")
PNG = _png_output.getvalue()


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


class FlakyLeaderboard:
    def __init__(self) -> None:
        self.fail = True
        self.submit_calls = 0
        self.read_fail = False
        self.attempted_entries: list[LeaderboardEntry] = []
        self.entries: list[LeaderboardEntry] = []

    def submit(self, entry: LeaderboardEntry) -> SubmissionResult:
        self.submit_calls += 1
        self.attempted_entries.append(entry)
        if self.fail:
            raise LeaderboardWriteError("simulated retryable write failure")
        self.entries.append(entry)
        return SubmissionResult(reached_top_10=True, top_entries=tuple(self.entries))

    def get_top(
        self, scope: LeagueScope | str, duration: int
    ) -> tuple[LeaderboardEntry, ...]:
        if self.read_fail:
            raise LeaderboardReadError("simulated read failure")
        return tuple(
            entry
            for entry in self.entries
            if entry.scope == scope and entry.duration == duration
        )


def write_catalog(path: Path) -> None:
    crest_dir = path.parent / "crests"
    crest_dir.mkdir()
    clubs = []
    for scope_index, scope in enumerate(SUPPORTED_SCOPES[1:]):
        for number in range(4):
            provider_id = 3000 + (scope_index * 10) + number
            crest_name = f"crest-{scope_index}-{number}.png"
            (crest_dir / crest_name).write_bytes(PNG)
            clubs.append(
                {
                    "id": f"{scope}-leaderboard-{number}",
                    "provider_id": provider_id,
                    "name": f"Leaderboard Club {scope_index}-{number}",
                    "scope": scope,
                    "league": SUPPORTED_COMPETITIONS[scope][0],
                    "season": "2025/26",
                    "source_url": f"https://provider.test/{provider_id}",
                    "crest_path": f"crests/{crest_name}",
                }
            )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-08-26T12:00:00Z",
                "competitions": [
                    {
                        "scope": scope,
                        "name": details[0],
                        "code": details[1],
                        "season": "2025/26",
                    }
                    for scope, details in SUPPORTED_COMPETITIONS.items()
                ],
                "clubs": clubs,
            }
        ),
        encoding="utf-8",
    )


def make_settings(tmp_path: Path, data_path: Path) -> Settings:
    return Settings(
        environment="test",
        session_secret="leaderboard-test-secret",
        frontend_dist=tmp_path / "missing-dist",
        data_path=data_path,
        leaderboard_path=tmp_path / "leaderboard.sqlite3",
    )


def start_and_score(
    client: TestClient, *, scope: str = "all", duration: int = 30
) -> dict[str, object]:
    state = client.post(
        "/api/round/start", json={"scope": scope, "duration": duration}
    ).json()
    for _ in range(20):
        question = state["question"]
        for choice in list(question["choices"]):
            response = client.post(
                "/api/round/guess",
                json={
                    "question_token": question["question_token"],
                    "answer_token": choice["answer_token"],
                },
            )
            assert response.status_code == 200
            result = response.json()
            if not result["correct"]:
                continue
            if result["state"]["score"] > 0:
                return result
            client.post(
                "/api/round/advance",
                json={"advance_token": result["state"]["advance_token"]},
            )
            state = result["state"]
            break
    raise AssertionError("Expected a positive score from issued answers")


def test_positive_expiry_is_submitted_once_and_public_rows_hide_player_id(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    clock = Clock()
    app = create_app(
        make_settings(tmp_path, data_path), serve_frontend=False, clock=clock
    )
    with TestClient(app) as client:
        client.put("/api/player", json={"username": "Ranked Player"})
        scored = start_and_score(client)
        clock.now = 530.0

        result = client.get("/api/state").json()["round"]
        assert result["final_score"] == scored["state"]["score"]
        assert result["leaderboard_submission_pending"] is False
        assert result["made_top_10"] is True
        assert client.post("/api/round/expire").json() == result
        assert client.get("/api/state").json()["round"] == result

        leaderboard = client.get(
            "/api/leaderboard", params={"scope": "all", "duration": 30}
        )
        assert leaderboard.status_code == 200
        body = leaderboard.json()
        assert body["scope"] == "all"
        assert body["duration"] == 30
        assert len(body["entries"]) == 1
        assert body["entries"][0]["rank"] == 1
        assert body["entries"][0]["username"] == "Ranked Player"
        assert body["entries"][0]["is_current_player"] is True
        assert "player_id" not in leaderboard.text

        with TestClient(app) as anonymous:
            anonymous_board = anonymous.get(
                "/api/leaderboard", params={"scope": "all", "duration": 30}
            ).json()
        assert anonymous_board["entries"][0]["is_current_player"] is False
        assert "player_id" not in json.dumps(anonymous_board)


def test_same_session_can_submit_multiple_rounds_to_separate_boards(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    clock = Clock()
    with TestClient(
        create_app(
            make_settings(tmp_path, data_path), serve_frontend=False, clock=clock
        )
    ) as client:
        client.put("/api/player", json={"username": "Repeat Player"})
        start_and_score(client, scope="all", duration=30)
        clock.now = 530.0
        client.post("/api/round/expire")

        clock.now = 600.0
        start_and_score(client, scope="premier-league", duration=60)
        clock.now = 660.0
        client.post("/api/round/expire")

        all_board = client.get(
            "/api/leaderboard", params={"scope": "all", "duration": 30}
        ).json()["entries"]
        league_board = client.get(
            "/api/leaderboard",
            params={"scope": "premier-league", "duration": 60},
        ).json()["entries"]
        empty_board = client.get(
            "/api/leaderboard", params={"scope": "all", "duration": 60}
        ).json()["entries"]
        assert [row["username"] for row in all_board] == ["Repeat Player"]
        assert [row["username"] for row in league_board] == ["Repeat Player"]
        assert empty_board == []


def test_failed_automatic_submission_is_pending_and_retryable_once(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    clock = Clock()
    flaky = FlakyLeaderboard()
    app_settings = make_settings(tmp_path, data_path)
    submitted_at_calls = 0

    def submitted_at_clock() -> datetime:
        nonlocal submitted_at_calls
        submitted_at_calls += 1
        return datetime(2026, 1, 2, 0, 0, submitted_at_calls, tzinfo=UTC)

    service = GameService(
        data_path,
        app_settings.leaderboard_path,
        clock=clock,
        submitted_at_clock=submitted_at_clock,
        leaderboard=flaky,
    )
    with TestClient(
        create_app(app_settings, serve_frontend=False, game_service=service)
    ) as client:
        client.put("/api/player", json={"username": "Retry Player"})
        start_and_score(client)
        clock.now = 530.0

        pending = client.post("/api/round/expire")
        assert pending.status_code == 200
        assert pending.json()["leaderboard_submission_pending"] is True
        assert pending.json()["made_top_10"] is False
        assert flaky.submit_calls == 1
        client.get("/api/state")
        client.post("/api/round/expire")
        assert flaky.submit_calls == 1

        flaky.fail = False
        retried = client.post("/api/leaderboard/retry")
        assert retried.status_code == 200
        assert retried.json()["leaderboard_submission_pending"] is False
        assert retried.json()["made_top_10"] is True
        assert flaky.submit_calls == 2
        assert submitted_at_calls == 1
        assert flaky.attempted_entries[0] == flaky.attempted_entries[1]
        assert len(flaky.entries) == 1
        saved = flaky.entries[0]
        assert saved.username == "Retry Player"
        assert saved.round_id == retried.json()["round_token"]
        assert saved.scope == cast(LeagueScope, "all")
        assert saved.submitted_at == datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC)
        assert client.post("/api/leaderboard/retry").status_code == 409


def test_successful_leaderboard_read_clears_transient_error(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    flaky = FlakyLeaderboard()
    flaky.fail = False
    flaky.read_fail = True
    service = GameService(
        data_path,
        tmp_path / "leaderboard.sqlite3",
        leaderboard=flaky,
    )

    with pytest.raises(LeaderboardReadError):
        flaky.get_top("all", 30)
    with pytest.raises(ServiceUnavailableError, match="Leaderboard unavailable"):
        service.leaderboard("all", 30)
    assert service.service_status()["status"] == "degraded"

    flaky.read_fail = False
    assert service.leaderboard("all", 30)["entries"] == []
    assert service.service_status()["status"] == "ready"
    assert service.service_status()["detail"] is None


def test_leaderboard_query_validation_is_clear(tmp_path: Path) -> None:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    with TestClient(
        create_app(make_settings(tmp_path, data_path), serve_frontend=False)
    ) as client:
        bad_scope = client.get(
            "/api/leaderboard",
            params={"scope": "champions-league", "duration": 30},
        )
        bad_duration = client.get(
            "/api/leaderboard", params={"scope": "all", "duration": 45}
        )
        assert bad_scope.status_code == 422
        assert "Input should be" in bad_scope.json()["detail"]
        assert bad_duration.status_code == 400
        assert "unsupported leaderboard duration" in (
            bad_duration.json()["detail"].lower()
        )
