from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.models import SUPPORTED_COMPETITIONS, SUPPORTED_SCOPES

_png_output = io.BytesIO()
Image.new("RGBA", (256, 256), (20, 80, 160, 255)).save(_png_output, "PNG")
PNG = _png_output.getvalue()
_covered_png_output = io.BytesIO()
Image.new("RGBA", (256, 256), (180, 30, 40, 255)).save(_covered_png_output, "PNG")
COVERED_PNG = _covered_png_output.getvalue()


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def write_catalog(path: Path) -> None:
    crest_dir = path.parent / "crests"
    covered_crest_dir = path.parent / "covered-crests"
    crest_dir.mkdir()
    covered_crest_dir.mkdir()
    clubs = []
    for scope_index, scope in enumerate(SUPPORTED_SCOPES[1:]):
        for number in range(4):
            provider_id = 2000 + (scope_index * 10) + number
            crest_name = f"secret-{provider_id}.png"
            covered_crest_name = f"covered-{scope_index}-{number}.png"
            (crest_dir / crest_name).write_bytes(PNG)
            (covered_crest_dir / covered_crest_name).write_bytes(COVERED_PNG)
            clubs.append(
                {
                    "id": f"private-{scope}-{number}",
                    "provider_id": provider_id,
                    "name": f"Public Club {scope_index}-{number}",
                    "scope": scope,
                    "league": SUPPORTED_COMPETITIONS[scope][0],
                    "season": "2025/26",
                    "source_url": f"https://provider.test/private/{provider_id}",
                    "crest_path": f"crests/{crest_name}",
                    "covered_crest": f"covered-crests/{covered_crest_name}",
                    "theme_colors": {
                        "primary": "#B41E28",
                        "secondary": "#FFFFFF",
                    },
                    "cover_status": "covered",
                    "cover_regions": [
                        {
                            "x": 0.2,
                            "y": 0.6,
                            "width": 0.6,
                            "height": 0.15,
                            "shape": "rounded_rectangle",
                        }
                    ],
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


@pytest.fixture
def app_client(tmp_path: Path) -> tuple[object, TestClient, Clock]:
    data_path = tmp_path / "clubs.json"
    write_catalog(data_path)
    settings = Settings(
        environment="test",
        session_secret="security-test-secret",
        frontend_dist=tmp_path / "missing-dist",
        data_path=data_path,
        leaderboard_path=tmp_path / "leaderboard.sqlite3",
    )
    clock = Clock()
    app = create_app(settings, serve_frontend=False, clock=clock)
    with TestClient(app) as client:
        yield app, client, clock


def start(client: TestClient, username: str) -> dict[str, object]:
    client.put("/api/player", json={"username": username})
    response = client.post("/api/round/start", json={"scope": "all", "duration": 30})
    assert response.status_code == 200
    return response.json()


def test_play_state_and_crest_url_do_not_leak_private_answer_data(
    app_client: tuple[object, TestClient, Clock],
) -> None:
    _app, client, _clock = app_client
    state = start(client, "Secure Player")
    encoded = json.dumps(state)
    question = state["question"]
    crest_url = question["crest_url"]

    assert "provider_id" not in encoded
    assert "source_url" not in encoded
    assert "crest_path" not in encoded
    assert "private-" not in encoded
    assert "secret-" not in encoded
    assert state["reveal"] is None
    assert set(question) == {
        "question_token",
        "crest_url",
        "choices",
        "removed_answer_tokens",
        "round_number",
    }
    assert urlparse(crest_url).path == (
        f"/api/questions/{question['question_token']}/crest"
    )
    assert all(
        set(choice) == {"answer_token", "name", "league"}
        for choice in question["choices"]
    )

    image = client.get(crest_url)
    assert image.status_code == 200
    assert image.content == COVERED_PNG
    assert image.headers["cache-control"] == "no-store"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["content-type"].startswith("image/png")


def test_question_and_answer_tokens_are_bound_to_the_signed_session(
    app_client: tuple[object, TestClient, Clock],
) -> None:
    app, first_client, _clock = app_client
    first = start(first_client, "First")
    with TestClient(app) as second_client:
        second = start(second_client, "Second")
        first_question = first["question"]
        second_question = second["question"]

        cross_crest = second_client.get(first_question["crest_url"])
        assert cross_crest.status_code == 400
        assert "question token" in cross_crest.json()["detail"].lower()

        cross_question = first_client.post(
            "/api/round/guess",
            json={
                "question_token": second_question["question_token"],
                "answer_token": second_question["choices"][0]["answer_token"],
            },
        )
        assert cross_question.status_code == 400
        assert "question token" in cross_question.json()["detail"].lower()

        cross_answer = first_client.post(
            "/api/round/guess",
            json={
                "question_token": first_question["question_token"],
                "answer_token": second_question["choices"][0]["answer_token"],
            },
        )
        assert cross_answer.status_code == 400
        assert "answer token" in cross_answer.json()["detail"].lower()


def test_answer_tokens_cannot_be_mixed_across_questions(
    app_client: tuple[object, TestClient, Clock],
) -> None:
    _app, client, _clock = app_client
    first = start(client, "Question Bound")
    first_question = first["question"]
    correct = None
    for choice in list(first_question["choices"]):
        response = client.post(
            "/api/round/guess",
            json={
                "question_token": first_question["question_token"],
                "answer_token": choice["answer_token"],
            },
        )
        if response.json()["correct"]:
            correct = response.json()
            break
    assert correct is not None
    second_question = correct["state"]["question"]
    client.post(
        "/api/round/advance",
        json={"advance_token": correct["state"]["advance_token"]},
    )

    stale_question = client.post(
        "/api/round/guess",
        json={
            "question_token": first_question["question_token"],
            "answer_token": second_question["choices"][0]["answer_token"],
        },
    )
    assert stale_question.status_code == 400
    assert "already been answered" in stale_question.json()["detail"]

    old_answer = client.post(
        "/api/round/guess",
        json={
            "question_token": second_question["question_token"],
            "answer_token": correct["reveal"]["answer_token"],
        },
    )
    assert old_answer.status_code == 400
    assert "answer token" in old_answer.json()["detail"].lower()


def test_unknown_crest_and_unsigned_session_cannot_read_assets(
    app_client: tuple[object, TestClient, Clock],
) -> None:
    app, client, _clock = app_client
    state = start(client, "Owner")
    assert client.get("/api/questions/not-issued/crest").status_code == 400

    with TestClient(app) as anonymous:
        response = anonymous.get(state["question"]["crest_url"])
        assert response.status_code == 401
        assert "player" in response.json()["detail"].lower()


def test_original_crest_is_available_only_after_a_correct_answer(
    app_client: tuple[object, TestClient, Clock],
) -> None:
    _app, client, _clock = app_client
    state = start(client, "Reveal Security")
    question = state["question"]
    crest_url = question["crest_url"]

    assert client.get(f"{crest_url}?reveal=guessed").content == COVERED_PNG
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
        if response.json()["correct"]:
            correct = response.json()
            break

    assert correct is not None
    assert correct["reveal"]["crest_url"] == crest_url
    assert client.get(f"{crest_url}?reveal=resolved").content == PNG
    assert client.get(correct["state"]["question"]["crest_url"]).content == COVERED_PNG
    encoded = json.dumps(correct)
    assert "secret-" not in encoded
    assert "covered-crests" not in encoded
    assert "provider_id" not in encoded


def test_expiry_reveals_original_through_the_same_opaque_route(
    app_client: tuple[object, TestClient, Clock],
) -> None:
    _app, client, clock = app_client
    state = start(client, "Expiry Security")
    active_url = state["question"]["crest_url"]
    assert client.get(active_url).content == COVERED_PNG

    clock.now = 130.0
    expired = client.post("/api/round/expire")

    assert expired.status_code == 200
    reveal = expired.json()["final_unanswered_club"]
    assert reveal["crest_url"] == active_url
    assert client.get(f"{active_url}?reveal=expired").content == PNG
