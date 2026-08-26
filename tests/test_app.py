from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    data_path = tmp_path / "clubs.json"
    data_path.write_text(
        '{"schema_version": 1, "competitions": [], "clubs": []}',
        encoding="utf-8",
    )
    settings = Settings(
        environment="test",
        session_secret="test-secret",
        frontend_dist=tmp_path / "missing-dist",
        data_path=data_path,
        leaderboard_path=tmp_path / "leaderboard.sqlite3",
    )
    with TestClient(create_app(settings, serve_frontend=False)) as test_client:
        yield test_client


def test_state_describes_supported_game_options(client: TestClient) -> None:
    response = client.get("/api/state")

    assert response.status_code == 200
    body = response.json()
    assert body["player"] is None
    assert body["round"] is None
    assert body["supported_scopes"] == [
        "all",
        "premier-league",
        "bundesliga",
        "la-liga",
        "primeira-liga",
        "ligue-1",
        "serie-a",
        "eredivisie",
    ]
    assert body["supported_durations"] == [30, 60, 90]
    assert body["service"]["status"] == "setup-required"
    assert body["service"]["data_ready"] is False
    assert body["service"]["leaderboard_ready"] is True
    assert "retry setup" in body["service"]["detail"]


def test_player_name_is_normalized_and_restored_from_session(
    client: TestClient,
) -> None:
    update_response = client.put("/api/player", json={"username": "  Ada   Lovelace "})

    assert update_response.status_code == 200
    assert update_response.json()["player"] == {"username": "Ada Lovelace"}
    assert client.get("/api/state").json()["player"] == {"username": "Ada Lovelace"}
    assert "crest_quest_session" in client.cookies


def test_invalid_player_name_returns_a_safe_message(client: TestClient) -> None:
    response = client.put("/api/player", json={"username": " " * 3})

    assert response.status_code == 422
    assert response.json() == {"detail": "Enter a player name."}


def test_logout_clears_player_session(client: TestClient) -> None:
    client.put("/api/player", json={"username": "Alex"})

    response = client.post("/api/player/logout")

    assert response.status_code == 204
    assert client.get("/api/state").json()["player"] is None


def test_state_does_not_expose_secrets(client: TestClient) -> None:
    body = client.get("/api/state").text

    assert "test-secret" not in body
    assert "FOOTBALL_DATA_API_KEY" not in body


def test_liveness_is_unconditional_but_health_requires_club_data(
    client: TestClient,
) -> None:
    live = client.get("/api/live")
    health = client.get("/api/health")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert health.status_code == 503
    assert health.json() == {"status": "not-ready"}
