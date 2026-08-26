from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

if __package__ in {None, ""}:
    # Make the project namespace importable during direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prepare_crest_assets import (
    DEFAULT_METADATA as DEFAULT_COVER_METADATA,
)
from scripts.prepare_crest_assets import (
    prepare_crest_assets,
)
from scripts.validate_data import validate_data
from src.crest_covering import CrestCoverError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "var" / "football-data-cache"
API_BASE_URL = "https://api.football-data.org/v4"
API_KEY_ENVIRONMENT_VARIABLE = "FOOTBALL_DATA_API_KEY"
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
MAX_CREST_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class CompetitionSpec:
    scope: str
    name: str
    expected_code: str
    area: str
    provider_names: tuple[str, ...]


COMPETITION_SPECS = (
    CompetitionSpec(
        "premier-league",
        "Premier League",
        "PL",
        "England",
        ("Premier League",),
    ),
    CompetitionSpec(
        "bundesliga",
        "Bundesliga",
        "BL1",
        "Germany",
        ("Bundesliga",),
    ),
    CompetitionSpec(
        "la-liga",
        "La Liga",
        "PD",
        "Spain",
        ("Primera Division", "Primera División", "La Liga"),
    ),
    CompetitionSpec(
        "primeira-liga",
        "Primeira Liga",
        "PPL",
        "Portugal",
        ("Primeira Liga",),
    ),
    CompetitionSpec(
        "ligue-1",
        "Ligue 1",
        "FL1",
        "France",
        ("Ligue 1",),
    ),
    CompetitionSpec(
        "serie-a",
        "Serie A",
        "SA",
        "Italy",
        ("Serie A",),
    ),
    CompetitionSpec(
        "eredivisie",
        "Eredivisie",
        "DED",
        "Netherlands",
        ("Eredivisie",),
    ),
)


class ImportFailure(RuntimeError):
    def __init__(self, message: str, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


@dataclass(frozen=True)
class ResolvedCompetition:
    spec: CompetitionSpec
    provider_id: int
    code: str


@dataclass(frozen=True)
class ImportSummary:
    competitions: int
    clubs: int
    downloaded_crests: int
    cached_crests: int
    manifest_path: Path


class RetryingHttpClient:
    """Small retrying HTTP adapter that is straightforward to mock in tests."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        attempts: int = 4,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")
        self.client = client
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        last_network_error: httpx.RequestError | None = None
        for attempt in range(self.attempts):
            try:
                response = self.client.get(
                    url,
                    headers=headers,
                    params=params,
                    follow_redirects=False,
                )
            except httpx.RequestError as exc:
                last_network_error = exc
                if attempt + 1 == self.attempts:
                    break
                self.sleep(self.backoff_seconds * (2**attempt))
                continue

            if 200 <= response.status_code < 300:
                return response
            if (
                response.status_code not in RETRYABLE_STATUS_CODES
                or attempt + 1 == self.attempts
            ):
                raise ImportFailure(
                    f"HTTP {response.status_code} while requesting {_safe_url(url)}"
                )
            self.sleep(_retry_delay(response, self.backoff_seconds, attempt))

        if last_network_error is not None:
            raise ImportFailure(
                f"network request failed after {self.attempts} attempt(s): "
                f"{_safe_url(url)} ({type(last_network_error).__name__})"
            ) from last_network_error
        raise ImportFailure(f"request failed: {_safe_url(url)}")

    def get_bytes(self, url: str, *, max_bytes: int) -> bytes:
        """Stream a response up to max_bytes without forwarding API auth."""
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")

        last_network_error: httpx.RequestError | None = None
        for attempt in range(self.attempts):
            response: httpx.Response | None = None
            delay = self.backoff_seconds * (2**attempt)
            try:
                request = self.client.build_request("GET", url)
                request.headers.pop("X-Auth-Token", None)
                response = self.client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                )

                if 200 <= response.status_code < 300:
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared_bytes = int(content_length)
                        except ValueError:
                            declared_bytes = -1
                        if declared_bytes > max_bytes:
                            raise ImportFailure(
                                f"response exceeds {max_bytes} bytes: {_safe_url(url)}"
                            )

                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if len(content) + len(chunk) > max_bytes:
                            raise ImportFailure(
                                f"response exceeds {max_bytes} bytes: {_safe_url(url)}"
                            )
                        content.extend(chunk)
                    return bytes(content)

                if (
                    response.status_code not in RETRYABLE_STATUS_CODES
                    or attempt + 1 == self.attempts
                ):
                    raise ImportFailure(
                        f"HTTP {response.status_code} while requesting {_safe_url(url)}"
                    )
                delay = _retry_delay(response, self.backoff_seconds, attempt)
            except httpx.RequestError as exc:
                last_network_error = exc
                if attempt + 1 == self.attempts:
                    break
            finally:
                if response is not None:
                    response.close()

            self.sleep(delay)

        if last_network_error is not None:
            raise ImportFailure(
                f"network request failed after {self.attempts} attempt(s): "
                f"{_safe_url(url)} ({type(last_network_error).__name__})"
            ) from last_network_error
        raise ImportFailure(f"request failed: {_safe_url(url)}")


class FootballDataApi:
    def __init__(
        self,
        http: RetryingHttpClient,
        api_key: str,
        base_url: str = API_BASE_URL,
        *,
        minimum_interval_seconds: float = 6.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("api_key cannot be empty")
        if minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds cannot be negative")
        self.http = http
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.minimum_interval_seconds = minimum_interval_seconds
        self.sleep = sleep
        self.clock = clock
        self._last_request_started: float | None = None

    def get_json(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        now = self.clock()
        if self._last_request_started is not None:
            delay = self.minimum_interval_seconds - (now - self._last_request_started)
            if delay > 0:
                self.sleep(delay)
        self._last_request_started = self.clock()

        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.http.get(
            url,
            headers={"X-Auth-Token": self.api_key},
            params=params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ImportFailure(
                f"football-data.org returned invalid JSON for {_safe_url(url)}"
            ) from exc
        if not isinstance(payload, dict):
            raise ImportFailure(
                f"football-data.org returned a non-object payload for {_safe_url(url)}"
            )
        return payload


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "<invalid URL>"
    if not parsed.scheme or hostname is None:
        return "<invalid URL>"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{display_host}:{port}" if port is not None else display_host
    path = parsed.path or "/"
    return f"{parsed.scheme}://{authority}{path}"


def _retry_delay(
    response: httpx.Response, backoff_seconds: float, attempt: int
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    if response.status_code == 429:
        return 60.0
    return backoff_seconds * (2**attempt)


def _normalized_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _provider_area_name(competition: Mapping[str, Any]) -> str:
    area = competition.get("area")
    if isinstance(area, Mapping):
        return _normalized_name(area.get("name"))
    return ""


def resolve_competitions(catalogue: Mapping[str, Any]) -> list[ResolvedCompetition]:
    """Resolve and verify all supported competitions from GET /v4/competitions."""
    raw_competitions = catalogue.get("competitions")
    if not isinstance(raw_competitions, list):
        raise ImportFailure("GET /v4/competitions did not contain a competitions list")

    resolved: list[ResolvedCompetition] = []
    for spec in COMPETITION_SPECS:
        names = {_normalized_name(name) for name in spec.provider_names}
        candidates = [
            item
            for item in raw_competitions
            if isinstance(item, Mapping)
            and _provider_area_name(item) == _normalized_name(spec.area)
            and _normalized_name(item.get("name")) in names
        ]
        if not candidates:
            raise ImportFailure(
                f"could not resolve {spec.name} by provider name and area {spec.area!r}"
            )
        if len(candidates) > 1:
            descriptions = ", ".join(
                f"{item.get('name')!r} ({item.get('code')!r})" for item in candidates
            )
            raise ImportFailure(
                f"competition catalogue is ambiguous for {spec.name}: {descriptions}"
            )

        competition = candidates[0]
        code = competition.get("code")
        if not isinstance(code, str) or code != spec.expected_code:
            raise ImportFailure(
                f"verified provider code for {spec.name} is {code!r}, "
                f"expected {spec.expected_code!r}"
            )
        provider_id = competition.get("id")
        if not isinstance(provider_id, int) or isinstance(provider_id, bool):
            raise ImportFailure(f"provider competition ID is invalid for {spec.name}")
        resolved.append(ResolvedCompetition(spec, provider_id, code))
    return resolved


def _season_label(season: Mapping[str, Any], competition_name: str) -> str:
    start_date = season.get("startDate")
    end_date = season.get("endDate")
    if not isinstance(start_date, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", start_date
    ):
        raise ImportFailure(
            f"current season start date is invalid for {competition_name}"
        )
    if not isinstance(end_date, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", end_date
    ):
        raise ImportFailure(
            f"current season end date is invalid for {competition_name}"
        )
    return f"{start_date[:4]}/{end_date[:4]}"


def _stable_club_id(provider_id: int) -> str:
    digest = hashlib.sha256(
        f"crest-quest:football-data:club:{provider_id}".encode()
    ).hexdigest()
    return f"club_{digest[:24]}"


def _stable_crest_path(provider_id: int) -> str:
    digest = hashlib.sha256(
        f"crest-quest:football-data:crest:v1:{provider_id}".encode()
    ).hexdigest()
    return f"crests/{digest[:32]}.png"


def _cache_path(cache_dir: Path, source_url: str) -> Path:
    digest = hashlib.sha256(source_url.encode()).hexdigest()
    return cache_dir / f"{digest}.source"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_crest_source_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ImportFailure(
            f"invalid crest source URL: {_safe_url(source_url)}"
        ) from exc

    if parsed.scheme != "https" or not parsed.netloc or hostname is None:
        raise ImportFailure(f"crest source URL must use HTTPS: {_safe_url(source_url)}")
    if parsed.username is not None or parsed.password is not None:
        raise ImportFailure(
            f"crest source URL must not contain credentials: {_safe_url(source_url)}"
        )

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ImportFailure(
            f"crest source URL uses a non-public IP address: {_safe_url(source_url)}"
        )


def acquire_crest(
    source_url: str,
    cache_dir: Path,
    http: RetryingHttpClient,
    *,
    refresh: bool = False,
) -> tuple[bytes, bool, Path]:
    """Return crest bytes, whether the cache was used, and the cache path."""
    _validate_crest_source_url(source_url)

    cached_path = _cache_path(cache_dir, source_url)
    if not refresh and cached_path.is_file():
        cached_size = cached_path.stat().st_size
        if cached_size > MAX_CREST_BYTES:
            cached_path.unlink(missing_ok=True)
            raise ImportFailure(
                f"cached crest exceeds {MAX_CREST_BYTES} bytes: {_safe_url(source_url)}"
            )
        with cached_path.open("rb") as cached_file:
            content = cached_file.read(MAX_CREST_BYTES + 1)
        if len(content) > MAX_CREST_BYTES:
            cached_path.unlink(missing_ok=True)
            raise ImportFailure(
                f"cached crest exceeds {MAX_CREST_BYTES} bytes: {_safe_url(source_url)}"
            )
        if content:
            return content, True, cached_path
        cached_path.unlink(missing_ok=True)

    content = http.get_bytes(source_url, max_bytes=MAX_CREST_BYTES)
    if not content:
        raise ImportFailure(f"empty crest response from {_safe_url(source_url)}")
    _atomic_write(cached_path, content)
    return content, False, cached_path


def _looks_like_svg(content: bytes, source_url: str) -> bool:
    sample = content[:2048].lstrip().lower()
    return b"<svg" in sample or urlparse(source_url).path.lower().endswith(".svg")


def normalize_crest(content: bytes, source_url: str, destination: Path) -> None:
    """Normalize a raster image or SVG to a centered, transparent 256x256 PNG."""
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise ImportFailure("Pillow is required to normalize crest images") from exc

    image_bytes = content
    if _looks_like_svg(content, source_url):
        try:
            cairosvg = importlib.import_module("cairosvg")
        except ImportError as exc:
            raise ImportFailure("CairoSVG is required to normalize SVG crests") from exc
        try:
            image_bytes = cairosvg.svg2png(bytestring=content)
        except Exception as exc:  # CairoSVG exposes several parser/backend errors.
            raise ImportFailure(
                f"invalid SVG crest from {_safe_url(source_url)}"
            ) from exc

    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise ImportFailure(
            f"invalid crest image from {_safe_url(source_url)}"
        ) from exc

    if image.width < 1 or image.height < 1:
        raise ImportFailure(f"crest has invalid dimensions: {_safe_url(source_url)}")
    scale = min(256 / image.width, 256 / image.height)
    resized_size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    image = image.resize(resized_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    offset = ((256 - image.width) // 2, (256 - image.height) // 2)
    canvas.alpha_composite(image, offset)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        canvas.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _competition_details(
    api: FootballDataApi, resolved: ResolvedCompetition
) -> tuple[dict[str, Any], Mapping[str, Any], str]:
    details = api.get_json(f"competitions/{resolved.code}")
    if details.get("code") != resolved.code:
        raise ImportFailure(
            f"competition detail code mismatch for {resolved.spec.name}: "
            f"{details.get('code')!r}"
        )
    current_season = details.get("currentSeason")
    if not isinstance(current_season, Mapping):
        raise ImportFailure(f"current season is missing for {resolved.spec.name}")
    season = _season_label(current_season, resolved.spec.name)
    return details, current_season, season


def _teams_for_current_season(
    api: FootballDataApi,
    resolved: ResolvedCompetition,
    current_season: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    start_date = current_season["startDate"]
    payload = api.get_json(
        f"competitions/{resolved.code}/teams",
        params={"season": str(start_date)[:4]},
    )
    payload_competition = payload.get("competition")
    if not isinstance(payload_competition, Mapping) or (
        payload_competition.get("code") != resolved.code
    ):
        raise ImportFailure(
            f"teams response competition mismatch for {resolved.spec.name}"
        )
    payload_season = payload.get("season")
    if isinstance(payload_season, Mapping):
        expected_id = current_season.get("id")
        actual_id = payload_season.get("id")
        if (
            expected_id is not None
            and actual_id is not None
            and expected_id != actual_id
        ):
            raise ImportFailure(
                f"teams response season mismatch for {resolved.spec.name}"
            )
    teams = payload.get("teams")
    if not isinstance(teams, list):
        raise ImportFailure(f"teams response is missing teams for {resolved.spec.name}")
    return [team for team in teams if isinstance(team, Mapping)]


def _generated_at(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _publish(stage_dir: Path, output_dir: Path, manifest: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_by_directory: dict[str, set[str]] = {}
    for directory_name in ("crests", "covered-crests"):
        output_assets = output_dir / directory_name
        output_assets.mkdir(parents=True, exist_ok=True)
        expected_names: set[str] = set()
        for source in (stage_dir / directory_name).glob("*.png"):
            expected_names.add(source.name)
            os.replace(source, output_assets / source.name)
        expected_by_directory[directory_name] = expected_names

    manifest_path = output_dir / "clubs.json"
    manifest_bytes = (
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    ).encode()
    _atomic_write(manifest_path, manifest_bytes)

    # Remove stale assets only after the new manifest is atomically visible.
    for directory_name, expected_names in expected_by_directory.items():
        for existing in (output_dir / directory_name).glob("*.png"):
            if existing.name not in expected_names:
                existing.unlink()
    return manifest_path


def import_football_data(
    *,
    api_key: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    client: httpx.Client | None = None,
    attempts: int = 4,
    backoff_seconds: float = 0.5,
    refresh_crests: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    timeout_seconds: float = 30.0,
    api_base_url: str = API_BASE_URL,
    api_interval_seconds: float = 6.5,
    cover_metadata_path: Path = DEFAULT_COVER_METADATA,
) -> ImportSummary:
    """Import all supported current competitions and atomically publish valid data."""
    if not api_key:
        raise ImportFailure(f"{API_KEY_ENVIRONMENT_VARIABLE} is not set")

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "CrestQuest-data-importer/1"},
        )
    http = RetryingHttpClient(
        client,
        attempts=attempts,
        backoff_seconds=backoff_seconds,
        sleep=sleep,
    )
    api = FootballDataApi(
        http,
        api_key,
        api_base_url,
        minimum_interval_seconds=api_interval_seconds,
        sleep=sleep,
    )

    output_dir = Path(output_dir)
    cache_dir = Path(cache_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    cached = 0
    issues: list[str] = []

    try:
        resolved_competitions = resolve_competitions(api.get_json("competitions"))
        competitions: list[dict[str, Any]] = []
        clubs: list[dict[str, Any]] = []

        with tempfile.TemporaryDirectory(
            prefix=".crestquest-import-", dir=output_dir.parent
        ) as temporary_dir:
            stage_dir = Path(temporary_dir)
            for resolved in resolved_competitions:
                _, current_season, season = _competition_details(api, resolved)
                competitions.append(
                    {
                        "scope": resolved.spec.scope,
                        "name": resolved.spec.name,
                        "code": resolved.code,
                        "season": season,
                    }
                )
                teams = _teams_for_current_season(api, resolved, current_season)
                if not teams:
                    issues.append(f"{resolved.spec.name}: provider returned no teams")
                    continue

                for team in teams:
                    provider_id = team.get("id")
                    name = team.get("name")
                    source_url = team.get("crest")
                    team_label = (
                        name.strip()
                        if isinstance(name, str) and name.strip()
                        else f"provider team {provider_id!r}"
                    )
                    if (
                        not isinstance(provider_id, int)
                        or isinstance(provider_id, bool)
                        or provider_id <= 0
                    ):
                        issues.append(
                            f"{resolved.spec.name}: {team_label} has an invalid "
                            "provider ID"
                        )
                        continue
                    if not isinstance(name, str) or not name.strip():
                        issues.append(
                            f"{resolved.spec.name}: provider team {provider_id} "
                            "has no name"
                        )
                        continue
                    if not isinstance(source_url, str) or not source_url.strip():
                        issues.append(
                            f"{resolved.spec.name}: {team_label} has no crest URL"
                        )
                        continue

                    cache_path: Path | None = None
                    try:
                        content, cache_hit, cache_path = acquire_crest(
                            source_url,
                            cache_dir,
                            http,
                            refresh=refresh_crests,
                        )
                        crest_path = _stable_crest_path(provider_id)
                        normalize_crest(content, source_url, stage_dir / crest_path)
                    except (ImportFailure, OSError) as exc:
                        if cache_path is not None:
                            cache_path.unlink(missing_ok=True)
                        issues.append(f"{resolved.spec.name}: {team_label}: {exc}")
                        continue

                    if cache_hit:
                        cached += 1
                    else:
                        downloaded += 1
                    clubs.append(
                        {
                            "id": _stable_club_id(provider_id),
                            "provider_id": provider_id,
                            "name": name.strip(),
                            "scope": resolved.spec.scope,
                            "league": resolved.spec.name,
                            "season": season,
                            "source_url": source_url,
                            "crest": crest_path,
                        }
                    )

            if issues:
                raise ImportFailure(
                    f"import found {len(issues)} missing or invalid "
                    "crest/team issue(s)",
                    issues,
                )

            clubs.sort(key=lambda club: (club["scope"], club["name"].casefold()))
            manifest = {
                "schema_version": 1,
                "generated_at": _generated_at(now),
                "competitions": competitions,
                "clubs": clubs,
            }
            staged_manifest = stage_dir / "clubs.json"
            staged_manifest.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            try:
                prepare_crest_assets(staged_manifest, cover_metadata_path)
            except CrestCoverError as error:
                raise ImportFailure(
                    f"crest-cover preparation failed: {error}"
                ) from error
            manifest = json.loads(staged_manifest.read_text(encoding="utf-8"))
            validation_errors = validate_data(staged_manifest)
            if validation_errors:
                raise ImportFailure(
                    f"staged import failed validation with "
                    f"{len(validation_errors)} error(s)",
                    validation_errors,
                )
            manifest_path = _publish(stage_dir, output_dir, manifest)

        return ImportSummary(
            competitions=len(competitions),
            clubs=len(clubs),
            downloaded_crests=downloaded,
            cached_crests=cached,
            manifest_path=manifest_path,
        )
    finally:
        if owns_client:
            client.close()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import current football-data.org v4 teams and normalize their crests. "
            f"The API key is read only from {API_KEY_ENVIRONMENT_VARIABLE}."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"packaged data directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"resumable source-image cache (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--attempts",
        type=_positive_int,
        default=4,
        help="maximum attempts for each HTTP request (default: 4)",
    )
    parser.add_argument(
        "--backoff-seconds",
        type=_non_negative_float,
        default=0.5,
        help="initial exponential retry delay (default: 0.5)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_non_negative_float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--api-interval-seconds",
        type=_non_negative_float,
        default=6.5,
        help=(
            "minimum delay between authenticated API calls; the default respects "
            "the 10 requests/minute plan limit"
        ),
    )
    parser.add_argument(
        "--refresh-crests",
        action="store_true",
        help="ignore cached source images and download every crest again",
    )
    parser.add_argument(
        "--cover-metadata",
        type=Path,
        default=DEFAULT_COVER_METADATA,
        help=(f"reviewed cover-region metadata (default: {DEFAULT_COVER_METADATA})"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.environ.get(API_KEY_ENVIRONMENT_VARIABLE, "")
    try:
        summary = import_football_data(
            api_key=api_key,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
            attempts=args.attempts,
            backoff_seconds=args.backoff_seconds,
            refresh_crests=args.refresh_crests,
            timeout_seconds=args.timeout_seconds,
            api_interval_seconds=args.api_interval_seconds,
            cover_metadata_path=args.cover_metadata,
        )
    except ImportFailure as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Import failed due to a file-system error: {exc}", file=sys.stderr)
        return 1

    print(
        "Import complete: "
        f"{summary.competitions} competitions, {summary.clubs} clubs, "
        f"{summary.downloaded_crests} downloaded crests, "
        f"{summary.cached_crests} cached crests."
    )
    print(f"Manifest: {summary.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
