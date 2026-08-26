from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.import_football_data as importer  # noqa: E402
from scripts.import_football_data import (  # noqa: E402
    API_BASE_URL,
    COMPETITION_SPECS,
    FootballDataApi,
    ImportFailure,
    RetryingHttpClient,
    import_football_data,
    normalize_crest,
    resolve_competitions,
)
from src.club_data import load_club_catalog  # noqa: E402


def _png_bytes(
    size: tuple[int, int] = (80, 40),
    color: tuple[int, int, int, int] = (20, 80, 160, 255),
) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def _catalogue() -> dict[str, object]:
    return {
        "competitions": [
            {
                "id": index,
                "code": spec.expected_code,
                "name": spec.provider_names[0],
                "area": {"name": spec.area},
            }
            for index, spec in enumerate(COMPETITION_SPECS, start=1)
        ]
    }


def _api_transport(
    *,
    invalid_code: str | None = None,
    missing_crest_code: str | None = None,
    invalid_image_code: str | None = None,
    reject_asset_requests: bool = False,
    seen_requests: list[httpx.Request] | None = None,
    crest_color: tuple[int, int, int, int] = (20, 80, 160, 255),
    team_ids: dict[str, int] | None = None,
) -> httpx.MockTransport:
    catalogue = _catalogue()
    if invalid_code is not None:
        competition = next(
            item for item in catalogue["competitions"] if item["code"] == invalid_code
        )
        competition["code"] = "WRONG"

    code_indexes = {
        spec.expected_code: index
        for index, spec in enumerate(COMPETITION_SPECS, start=1)
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if seen_requests is not None:
            seen_requests.append(request)
        if request.url.host == "assets.test":
            assert "X-Auth-Token" not in request.headers
            if reject_asset_requests:
                raise AssertionError("cached crest should not be downloaded")
            code = request.url.path.removeprefix("/").removesuffix(".png")
            content = (
                b"not an image"
                if code == invalid_image_code
                else _png_bytes(color=crest_color)
            )
            return httpx.Response(200, content=content)

        assert request.headers["X-Auth-Token"] == "super-secret-test-key"
        assert str(request.url).startswith(API_BASE_URL)
        path = request.url.path
        if path == "/v4/competitions":
            return httpx.Response(200, json=catalogue)

        code = path.split("/")[3]
        index = code_indexes[code]
        season = {
            "id": 9000 + index,
            "startDate": "2025-08-01",
            "endDate": "2026-05-31",
        }
        if path.endswith("/teams"):
            crest = (
                None
                if code == missing_crest_code
                else f"https://assets.test/{code}.png"
            )
            return httpx.Response(
                200,
                json={
                    "competition": {"code": code},
                    "season": season,
                    "teams": [
                        {
                            "id": (team_ids or {}).get(code, 1000 + index),
                            "name": f"{code} Example FC",
                            "crest": crest,
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={"id": index, "code": code, "currentSeason": season},
        )

    return httpx.MockTransport(handler)


def _run_import(
    tmp_path: Path,
    transport: httpx.MockTransport,
    **kwargs: object,
):
    metadata_team_ids = kwargs.pop("metadata_team_ids", {})
    assert isinstance(metadata_team_ids, dict)
    code_indexes = {
        spec.expected_code: index
        for index, spec in enumerate(COMPETITION_SPECS, start=1)
    }
    metadata_path = tmp_path / "crest-cover-metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clubs": [
                    {
                        "provider_team_id": metadata_team_ids.get(code, 1000 + index),
                        "review_status": "not_required",
                        "cover_regions": [],
                    }
                    for code, index in code_indexes.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    with httpx.Client(transport=transport) as client:
        return import_football_data(
            api_key="super-secret-test-key",
            output_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
            client=client,
            sleep=lambda _delay: None,
            now=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            cover_metadata_path=metadata_path,
            **kwargs,
        )


def test_resolves_all_supported_codes_from_catalogue() -> None:
    resolved = resolve_competitions(_catalogue())

    assert [(item.spec.scope, item.code) for item in resolved] == [
        ("premier-league", "PL"),
        ("bundesliga", "BL1"),
        ("la-liga", "PD"),
        ("primeira-liga", "PPL"),
        ("ligue-1", "FL1"),
        ("serie-a", "SA"),
        ("eredivisie", "DED"),
    ]


def test_rejects_a_provider_code_that_does_not_match_the_catalogue() -> None:
    catalogue = _catalogue()
    eredivisie = catalogue["competitions"][-1]
    eredivisie["code"] = "UNKNOWN"

    with pytest.raises(ImportFailure, match="expected 'DED'"):
        resolve_competitions(catalogue)


def test_http_requests_retry_with_exponential_backoff() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = RetryingHttpClient(
            client,
            attempts=3,
            backoff_seconds=0.25,
            sleep=delays.append,
        ).get("https://example.test/resource")

    assert response.status_code == 200
    assert calls == 3
    assert delays == [0.25, 0.5]


def test_rate_limit_without_retry_after_waits_for_provider_window() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429 if calls == 1 else 200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = RetryingHttpClient(
            client,
            attempts=2,
            backoff_seconds=0.25,
            sleep=delays.append,
        ).get("https://example.test/resource")

    assert response.status_code == 200
    assert delays == [60.0]


def test_api_redirect_does_not_forward_authentication() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.url.host == "api.football-data.org":
            assert request.headers["X-Auth-Token"] == "super-secret-test-key"
            return httpx.Response(
                302,
                headers={"Location": "https://redirect-target.test/collect"},
            )
        raise AssertionError("redirect target must not be requested")

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        api = FootballDataApi(
            RetryingHttpClient(client, attempts=1),
            "super-secret-test-key",
        )
        with pytest.raises(ImportFailure, match="HTTP 302"):
            api.get_json("competitions")

    assert [request.url.host for request in seen_requests] == ["api.football-data.org"]


def test_redirecting_crest_is_rejected_without_auth_leak(tmp_path: Path) -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert "X-Auth-Token" not in request.headers
        if request.url.host == "assets.test":
            return httpx.Response(
                307,
                headers={"Location": "https://redirect-target.test/crest.png"},
            )
        raise AssertionError("crest redirect target must not be requested")

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers={"X-Auth-Token": "super-secret-test-key"},
    ) as client:
        http = RetryingHttpClient(client, attempts=1)
        with pytest.raises(ImportFailure, match="HTTP 307"):
            importer.acquire_crest(
                "https://assets.test/crest.png",
                tmp_path / "cache",
                http,
            )

    assert [request.url.host for request in seen_requests] == ["assets.test"]


@pytest.mark.parametrize(
    "source_url",
    [
        "http://assets.test/crest.png",
        "https://127.0.0.1/crest.png",
        "https://10.0.0.1/crest.png",
        "https://169.254.10.20/crest.png",
        "https://192.0.2.10/crest.png",
        "https://[::1]/crest.png",
    ],
)
def test_unsafe_crest_urls_are_rejected(tmp_path: Path, source_url: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe crest URL must not be requested")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ImportFailure),
    ):
        importer.acquire_crest(
            source_url,
            tmp_path / "cache",
            RetryingHttpClient(client, attempts=1),
        )


def test_crest_url_credentials_are_rejected_and_redacted(tmp_path: Path) -> None:
    source_url = "https://crest-user:crest-password@assets.test/crest.png"

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("credential-bearing crest URL must not be requested")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ImportFailure) as caught,
    ):
        importer.acquire_crest(
            source_url,
            tmp_path / "cache",
            RetryingHttpClient(client, attempts=1),
        )

    message = str(caught.value)
    assert "crest-user" not in message
    assert "crest-password" not in message
    assert "https://assets.test/crest.png" in message


class _TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0
        self.exhausted = False

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        self.exhausted = True


def test_oversized_crest_response_is_stopped_while_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importer, "MAX_CREST_BYTES", 8)
    stream = _TrackingStream([b"12345", b"67890", b"not-read"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-Auth-Token" not in request.headers
        return httpx.Response(200, stream=stream)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ImportFailure, match="exceeds 8 bytes"),
    ):
        importer.acquire_crest(
            "https://assets.test/crest.png",
            tmp_path / "cache",
            RetryingHttpClient(client, attempts=1),
        )

    assert stream.yielded == 2
    assert not stream.exhausted
    assert not list((tmp_path / "cache").glob("*.source"))


def test_oversized_cached_crest_is_rejected_before_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importer, "MAX_CREST_BYTES", 8)
    source_url = "https://assets.test/crest.png"
    cache_dir = tmp_path / "cache"
    cached_path = importer._cache_path(cache_dir, source_url)
    cached_path.parent.mkdir(parents=True)
    cached_path.write_bytes(b"123456789")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("oversized cache entry must not be downloaded")

    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("oversized cache entry must not be opened")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        monkeypatch.context() as scoped_monkeypatch,
        pytest.raises(ImportFailure, match="cached crest exceeds 8 bytes"),
    ):
        scoped_monkeypatch.setattr(Path, "open", fail_open)
        importer.acquire_crest(
            source_url,
            cache_dir,
            RetryingHttpClient(client, attempts=1),
        )

    assert not cached_path.exists()


def test_import_normalizes_assets_and_resumes_from_cache(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    first = _run_import(
        tmp_path,
        _api_transport(seen_requests=requests),
    )

    assert first.competitions == len(COMPETITION_SPECS)
    assert first.clubs == len(COMPETITION_SPECS)
    assert first.downloaded_crests == len(COMPETITION_SPECS)
    assert first.cached_crests == 0
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["generated_at"] == "2026-01-02T03:04:05Z"
    assert {competition["scope"] for competition in manifest["competitions"]} == {
        spec.scope for spec in COMPETITION_SPECS
    }
    for club in manifest["clubs"]:
        filename = Path(club["crest"]).name
        assert filename.endswith(".png")
        assert filename != f"{club['provider_id']}.png"
        assert club["cover_status"] == "not_required"
        assert club["cover_regions"] == []
        assert club["covered_crest"] != club["crest"]
        assert set(club["theme_colors"]) == {"primary", "secondary"}
        with Image.open(tmp_path / "data" / club["crest"]) as image:
            assert image.size == (256, 256)
            assert image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 0
        with Image.open(tmp_path / "data" / club["covered_crest"]) as covered:
            assert covered.size == (256, 256)
            assert covered.mode == "RGBA"

    assert len(load_club_catalog(first.manifest_path).clubs) == len(COMPETITION_SPECS)
    assert all(
        "X-Auth-Token" not in request.headers
        for request in requests
        if request.url.host == "assets.test"
    )

    second = _run_import(
        tmp_path,
        _api_transport(reject_asset_requests=True),
    )
    assert second.downloaded_crests == 0
    assert second.cached_crests == len(COMPETITION_SPECS)


def test_refresh_crests_updates_content_for_the_same_url(tmp_path: Path) -> None:
    first = _run_import(tmp_path, _api_transport())
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    premier_league_club = next(
        club for club in manifest["clubs"] if club["scope"] == "premier-league"
    )
    crest_path = tmp_path / "data" / premier_league_club["crest"]
    with Image.open(crest_path) as image:
        assert image.getpixel((128, 128)) == (20, 80, 160, 255)

    second = _run_import(
        tmp_path,
        _api_transport(crest_color=(180, 30, 40, 255)),
        refresh_crests=True,
    )

    assert second.downloaded_crests == len(COMPETITION_SPECS)
    assert second.cached_crests == 0
    with Image.open(crest_path) as image:
        assert image.getpixel((128, 128)) == (180, 30, 40, 255)


def test_team_changes_remove_stale_published_crest_assets(tmp_path: Path) -> None:
    first = _run_import(tmp_path, _api_transport())
    first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    old_club = next(
        club for club in first_manifest["clubs"] if club["scope"] == "premier-league"
    )
    old_crest_path = tmp_path / "data" / old_club["crest"]
    assert old_crest_path.is_file()

    second = _run_import(
        tmp_path,
        _api_transport(team_ids={"PL": 99999}),
        metadata_team_ids={"PL": 99999},
    )
    second_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    replacement = next(
        club for club in second_manifest["clubs"] if club["scope"] == "premier-league"
    )

    assert replacement["provider_id"] == 99999
    assert replacement["crest"] != old_club["crest"]
    assert not old_crest_path.exists()
    assert (tmp_path / "data" / replacement["crest"]).is_file()
    assert len(list((tmp_path / "data" / "crests").glob("*.png"))) == len(
        COMPETITION_SPECS
    )


def test_invalid_crest_reports_failure_and_does_not_publish(tmp_path: Path) -> None:
    output_dir = tmp_path / "data"
    output_dir.mkdir()
    original = b'{"existing": true}\n'
    (output_dir / "clubs.json").write_bytes(original)

    with pytest.raises(ImportFailure) as caught:
        _run_import(
            tmp_path,
            _api_transport(invalid_image_code="PD"),
        )

    assert any("PD Example FC" in issue for issue in caught.value.issues)
    assert (output_dir / "clubs.json").read_bytes() == original
    assert not (output_dir / "crests").exists()
    # Invalid cached sources are removed, allowing a repaired source to work next run.
    assert len(list((tmp_path / "cache").glob("*.source"))) == (
        len(COMPETITION_SPECS) - 1
    )


def test_missing_crest_url_is_a_nonzero_import_failure(tmp_path: Path) -> None:
    with pytest.raises(ImportFailure) as caught:
        _run_import(
            tmp_path,
            _api_transport(missing_crest_code="SA"),
        )

    assert any("has no crest URL" in issue for issue in caught.value.issues)
    assert not (tmp_path / "data" / "clubs.json").exists()


def test_svg_normalization_preserves_aspect_ratio(tmp_path: Path) -> None:
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">
      <rect width="100" height="50" fill="red"/>
    </svg>"""
    destination = tmp_path / "crest.png"

    normalize_crest(svg, "https://assets.test/crest.svg", destination)

    with Image.open(destination) as image:
        assert image.mode == "RGBA"
        assert image.size == (256, 256)
        alpha = image.getchannel("A")
        assert alpha.getbbox() == (0, 64, 256, 192)
