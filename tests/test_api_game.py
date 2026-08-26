from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.game_service import GameService
from server.models import SUPPORTED_COMPETITIONS, SUPPORTED_SCOPES

_png_output = io.BytesIO()
Image.new("RGBA", (256, 256), (20, 80, 160, 0)).save(_png_output, "PNG")
PNG = _png_output.getvalue()


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def write_catalog(path: Path) -> None:
    crest_dir = path.parent / "crests"
    crest_dir.mkdir()
    clubs = []
    provider_id = 1000
    for scope in SUPPORTED_SCOPES[1:]:
        for number in range(4):
            provider_id += 1
            crest_name = f"{scope}-{number}.png"
            (crest_dir / crest_name).write_bytes(PNG)
            clubs.append(
                {
                    "id": f"{scope}-club-{number}",
                    "provider_id": provider_id,
                    "name": f"{scope} Club {number}",
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


def settings(tmp_path: Path, data_path: Path) -> Settings:
    return Settings(
        environment="test",
        session_secret="api-test-secret",
        frontend_dist=tmp_path / "missing-dist",
        data_path=data_path,
        leaderboard_path=tmp_path / "leaderboard.sqlite3",
    )


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, Clock]:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    clock = Clock()
    with TestClient(
        create_app(settings(tmp_path, data_path), serve_frontend=False, clock=clock)
    ) as client:
        yield client, clock


def guess_until_correct(
    client: TestClient, state: dict[str, object]
) -> dict[str, object]:
    question = state["question"]
    assert isinstance(question, dict)
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
        if result["correct"]:
            return result
    raise AssertionError("One of the four issued choices must be correct")


def test_round_flow_restores_state_and_reports_expiry(
    api: tuple[TestClient, Clock],
) -> None:
    client, clock = api
    assert (
        client.post(
            "/api/round/start", json={"scope": "all", "duration": 30}
        ).status_code
        == 401
    )
    client.put("/api/player", json={"username": "Alex"})

    started = client.post("/api/round/start", json={"scope": "all", "duration": 30})
    assert started.status_code == 200
    first = started.json()
    assert first["status"] == "active"
    assert first["round_token"]
    assert first["revision"] == 1
    assert "advance_token" not in first
    assert first["deadline"] == 130.0
    assert first["remaining_seconds"] == 30
    assert first["question"]["crest_url"] == (
        f"/api/questions/{first['question']['question_token']}/crest"
    )
    assert client.get("/api/state").json()["round"] == first
    assert client.post("/api/round/expire").status_code == 409

    correct = guess_until_correct(client, first)
    assert correct["points_awarded"] in {0, 1, 2, 3}
    assert correct["reveal"]["name"]
    assert correct["reveal"]["answer_token"]
    assert correct["state"]["awaiting_advance"] is True
    assert correct["state"]["advance_token"]
    assert correct["state"]["revision"] > first["revision"]
    assert (
        correct["state"]["question"]["question_token"]
        != (first["question"]["question_token"])
    )

    blocked = client.post(
        "/api/round/guess",
        json={
            "question_token": correct["state"]["question"]["question_token"],
            "answer_token": correct["state"]["question"]["choices"][0]["answer_token"],
        },
    )
    assert blocked.status_code == 409
    assert client.post("/api/round/advance").status_code == 422
    stale_advance = client.post(
        "/api/round/advance", json={"advance_token": "stale-advance"}
    )
    assert stale_advance.status_code == 400

    advance_token = correct["state"]["advance_token"]
    advanced = client.post(
        "/api/round/advance",
        json={"advance_token": advance_token},
    )
    assert advanced.status_code == 200
    assert advanced.json()["awaiting_advance"] is False
    assert "advance_token" not in advanced.json()
    assert advanced.json()["revision"] == correct["state"]["revision"] + 1
    assert advanced.json()["reveal"] is None
    reused_advance = client.post(
        "/api/round/advance", json={"advance_token": advance_token}
    )
    assert reused_advance.status_code == 400
    assert "already been used" in reused_advance.json()["detail"]

    clock.now = 130.0
    expired = client.post("/api/round/expire")
    assert expired.status_code == 200
    result = expired.json()
    assert result["status"] == "expired"
    assert result["round_token"] == first["round_token"]
    assert result["revision"] == advanced.json()["revision"] + 1
    assert result["final_score"] == correct["state"]["score"]
    assert result["clubs_named"] == 1
    assert result["best_streak"] in {0, 1}
    assert result["clean_three_bonuses"] == 0
    assert result["flawless_multiplier"] == 1
    assert result["final_unanswered_club"]["name"]
    assert result["leaderboard_submission_pending"] is False
    assert result["made_top_10"] is (result["final_score"] > 0)
    assert client.get("/api/state").json()["round"] == result


def test_wrong_reused_invalid_and_stale_tokens_are_rejected_clearly(
    api: tuple[TestClient, Clock],
) -> None:
    client, _clock = api
    client.put("/api/player", json={"username": "Token Tester"})
    state = client.post(
        "/api/round/start",
        json={"scope": "premier-league", "duration": 60},
    ).json()
    question = state["question"]

    invalid_question = client.post(
        "/api/round/guess",
        json={
            "question_token": "not-issued",
            "answer_token": question["choices"][0]["answer_token"],
        },
    )
    assert invalid_question.status_code == 400
    assert "question token" in invalid_question.json()["detail"].lower()
    invalid_answer = client.post(
        "/api/round/guess",
        json={
            "question_token": question["question_token"],
            "answer_token": "not-issued",
        },
    )
    assert invalid_answer.status_code == 400
    assert "answer token" in invalid_answer.json()["detail"].lower()

    wrong_token = None
    for _ in range(20):
        choice = question["choices"][0]
        response = client.post(
            "/api/round/guess",
            json={
                "question_token": question["question_token"],
                "answer_token": choice["answer_token"],
            },
        )
        if response.json()["correct"]:
            client.post(
                "/api/round/advance",
                json={"advance_token": response.json()["state"]["advance_token"]},
            )
            question = response.json()["state"]["question"]
            continue
        wrong_token = choice["answer_token"]
        reused = client.post(
            "/api/round/guess",
            json={
                "question_token": question["question_token"],
                "answer_token": wrong_token,
            },
        )
        assert reused.status_code == 400
        assert "already been used" in reused.json()["detail"]
        break
    assert wrong_token is not None


@pytest.mark.parametrize("scope", SUPPORTED_SCOPES)
@pytest.mark.parametrize("duration", (30, 60, 90))
def test_every_scope_and_duration_starts_a_playable_round(
    api: tuple[TestClient, Clock],
    scope: str,
    duration: int,
) -> None:
    client, _clock = api
    client.put("/api/player", json={"username": "Matrix Player"})

    response = client.post(
        "/api/round/start",
        json={"scope": scope, "duration": duration},
    )

    assert response.status_code == 200
    state = response.json()
    assert state["scope"] == scope
    assert state["duration_seconds"] == duration
    assert len(state["question"]["choices"]) == 4


def test_supported_options_are_validated_and_logout_removes_round(
    api: tuple[TestClient, Clock],
) -> None:
    client, _clock = api
    client.put("/api/player", json={"username": "Options"})
    for scope in SUPPORTED_SCOPES:
        response = client.post(
            "/api/round/start", json={"scope": scope, "duration": 90}
        )
        assert response.status_code == 200
        assert client.post("/api/player/logout").status_code == 204
        client.put("/api/player", json={"username": "Options"})

    assert (
        client.post(
            "/api/round/start", json={"scope": "champions-league", "duration": 30}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/round/start", json={"scope": "all", "duration": 45}
        ).status_code
        == 422
    )
    assert client.get("/api/state").json()["round"] is None


def test_setup_retry_recovers_after_catalog_is_installed(tmp_path: Path) -> None:
    data_path = tmp_path / "clubs.json"
    with TestClient(
        create_app(settings(tmp_path, data_path), serve_frontend=False)
    ) as client:
        initial = client.get("/api/state").json()
        assert initial["service"]["status"] == "setup-required"
        assert "retry setup" in initial["service"]["detail"]
        client.put("/api/player", json={"username": "Setup"})
        assert (
            client.post(
                "/api/round/start", json={"scope": "all", "duration": 30}
            ).status_code
            == 503
        )

        write_catalog(data_path)
        retried = client.post("/api/setup/retry")
        assert retried.status_code == 200
        assert retried.json()["service"]["status"] == "ready"
        assert retried.json()["service"]["data_ready"] is True
        assert client.get("/api/health").status_code == 200


def test_revision_increments_only_for_round_transitions(
    api: tuple[TestClient, Clock],
) -> None:
    client, clock = api
    client.put("/api/player", json={"username": "Revision Player"})
    state = client.post(
        "/api/round/start", json={"scope": "all", "duration": 30}
    ).json()
    round_token = state["round_token"]
    revision = 1

    assert client.get("/api/state").json()["round"]["revision"] == revision
    question = state["question"]
    correct = None
    for choice in question["choices"]:
        response = client.post(
            "/api/round/guess",
            json={
                "question_token": question["question_token"],
                "answer_token": choice["answer_token"],
            },
        )
        assert response.status_code == 200
        result = response.json()
        revision += 1
        assert result["state"]["revision"] == revision
        assert result["state"]["round_token"] == round_token
        assert client.get("/api/state").json()["round"]["revision"] == revision
        if result["correct"]:
            correct = result
            break

    assert correct is not None
    advanced = client.post(
        "/api/round/advance",
        json={"advance_token": correct["state"]["advance_token"]},
    ).json()
    revision += 1
    assert advanced["revision"] == revision

    clock.now = 130.0
    expired = client.get("/api/state").json()["round"]
    revision += 1
    assert expired["status"] == "expired"
    assert expired["round_token"] == round_token
    assert expired["revision"] == revision
    assert client.get("/api/state").json()["round"]["revision"] == revision


def test_reused_advance_token_cannot_consume_a_later_transition(
    api: tuple[TestClient, Clock],
) -> None:
    client, _clock = api
    client.put("/api/player", json={"username": "Advance Player"})
    first = client.post(
        "/api/round/start", json={"scope": "all", "duration": 30}
    ).json()
    first_correct = guess_until_correct(client, first)
    first_token = first_correct["state"]["advance_token"]
    advanced = client.post(
        "/api/round/advance", json={"advance_token": first_token}
    ).json()

    second_correct = guess_until_correct(client, advanced)
    second_state = second_correct["state"]
    second_token = second_state["advance_token"]
    duplicate = client.post("/api/round/advance", json={"advance_token": first_token})

    assert duplicate.status_code == 400
    restored = client.get("/api/state").json()["round"]
    assert restored["revision"] == second_state["revision"]
    assert restored["advance_token"] == second_token
    assert restored["awaiting_advance"] is True
    assert (
        client.post(
            "/api/round/advance", json={"advance_token": second_token}
        ).status_code
        == 200
    )


def test_round_retention_and_capacity_evict_abandoned_oldest_rounds(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    clock = Clock()
    service = GameService(
        data_path,
        tmp_path / "leaderboard.sqlite3",
        clock=clock,
        round_retention_seconds=10,
        max_rounds=2,
    )

    service.start_round("player-1", "One", "all", 30)
    clock.now = 101.0
    service.start_round("player-2", "Two", "all", 30)
    clock.now = 102.0
    assert service.state_for("player-1") is not None
    clock.now = 103.0
    service.start_round("player-3", "Three", "all", 30)

    assert service.round_count == 2
    assert service.state_for("player-1") is not None
    assert service.state_for("player-2") is None
    assert service.state_for("player-3") is not None

    clock.now = 114.0
    assert service.state_for("player-1") is None
    assert service.state_for("player-3") is None

    clock.now = 200.0
    service.start_round("expired-player", "Expired", "all", 30)
    for accessed_at in (209.0, 218.0, 227.0):
        clock.now = accessed_at
        assert service.state_for("expired-player")["status"] == "active"
    clock.now = 230.0
    assert service.state_for("expired-player")["status"] == "expired"
    clock.now = 241.0
    assert service.state_for("expired-player") is None


@pytest.mark.parametrize(
    "mode", ["missing-scope", "undersized-scope", "duplicate-names"]
)
def test_catalog_is_not_ready_without_four_distinct_names_per_scope(
    tmp_path: Path,
    mode: str,
) -> None:
    case_dir = tmp_path / mode
    case_dir.mkdir()
    data_path = case_dir / "clubs.json"
    write_catalog(data_path)
    manifest = json.loads(data_path.read_text(encoding="utf-8"))
    target_scope = SUPPORTED_SCOPES[1]
    target_clubs = [club for club in manifest["clubs"] if club["scope"] == target_scope]
    if mode == "missing-scope":
        manifest["clubs"] = [
            club for club in manifest["clubs"] if club["scope"] != target_scope
        ]
    elif mode == "undersized-scope":
        removed_id = target_clubs[-1]["id"]
        manifest["clubs"] = [
            club for club in manifest["clubs"] if club["id"] != removed_id
        ]
    else:
        for club in target_clubs:
            club["name"] = "Duplicate Display Name"
    data_path.write_text(json.dumps(manifest), encoding="utf-8")

    service = GameService(data_path, case_dir / "leaderboard.sqlite3")

    assert service.data_ready is False
    assert service.service_status()["status"] == "setup-required"


@pytest.mark.parametrize("mode", ["competition-code", "invalid-png"])
def test_catalog_readiness_rejects_invalid_metadata_and_assets(
    tmp_path: Path,
    mode: str,
) -> None:
    case_dir = tmp_path / mode
    case_dir.mkdir()
    data_path = case_dir / "clubs.json"
    write_catalog(data_path)
    manifest = json.loads(data_path.read_text(encoding="utf-8"))

    if mode == "competition-code":
        manifest["competitions"][0]["code"] = "WRONG"
        data_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        crest_path = case_dir / manifest["clubs"][0]["crest_path"]
        crest_path.write_bytes(b"not a png")

    service = GameService(data_path, case_dir / "leaderboard.sqlite3")

    assert service.data_ready is False
    assert service.service_status()["status"] == "setup-required"
