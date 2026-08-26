from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from src.crest_covering import (
    CrestCoverError,
    validate_cover_regions,
    validate_theme_colors,
)

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CLUB_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_SUPPORTED_CREST_SUFFIXES = frozenset(
    {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
)
_REQUIRED_CLUB_FIELDS = frozenset(
    {
        "id",
        "provider_id",
        "name",
        "scope",
        "league",
        "season",
        "source_url",
    }
)


class ClubDataError(ValueError):
    """Raised when packaged club data cannot be read or validated safely."""


@dataclass(frozen=True, slots=True, init=False)
class Club:
    """A validated club.

    ``id`` and ``provider_id`` are domain-only identifiers. They deliberately do not
    appear in the public dictionaries produced by the game engine.
    """

    id: str
    name: str
    scope: str
    league: str
    season: str
    source_url: str
    crest_path: str
    covered_crest_path: str
    theme_colors: Mapping[str, str]
    cover_regions: tuple[Mapping[str, Any], ...]
    cover_status: str
    _provider_id: int | str = field(repr=False)

    def __init__(
        self,
        *,
        id: str,
        provider_id: int | str,
        name: str,
        scope: str,
        league: str,
        season: str,
        source_url: str,
        crest_path: str,
        covered_crest_path: str | None = None,
        theme_colors: Mapping[str, str] | None = None,
        cover_regions: Iterable[Mapping[str, Any]] = (),
        cover_status: str = "not_required",
    ) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "league", league)
        object.__setattr__(self, "season", season)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "crest_path", crest_path)
        object.__setattr__(self, "covered_crest_path", covered_crest_path or crest_path)
        object.__setattr__(
            self,
            "theme_colors",
            MappingProxyType(
                dict(theme_colors or {"primary": "#143E34", "secondary": "#FFFFFF"})
            ),
        )
        object.__setattr__(
            self,
            "cover_regions",
            tuple(MappingProxyType(dict(region)) for region in cover_regions),
        )
        object.__setattr__(self, "cover_status", cover_status)
        object.__setattr__(self, "_provider_id", provider_id)

    @property
    def provider_id(self) -> int | str:
        """Return the upstream identifier for internal integrations only."""

        return self._provider_id


@dataclass(frozen=True, slots=True)
class ClubCatalog:
    """An immutable, indexed set of validated clubs."""

    clubs: tuple[Club, ...]
    _clubs_by_id: Mapping[str, Club] = field(repr=False)
    _clubs_by_scope: Mapping[str, tuple[Club, ...]] = field(repr=False)

    @classmethod
    def from_clubs(cls, clubs: Iterable[Club]) -> ClubCatalog:
        club_tuple = tuple(clubs)
        by_id: dict[str, Club] = {}
        by_scope: dict[str, list[Club]] = {}
        for club in club_tuple:
            if club.id in by_id:
                raise ClubDataError(f"Club id {club.id!r} is duplicated")
            by_id[club.id] = club
            by_scope.setdefault(club.scope, []).append(club)
        return cls(
            clubs=club_tuple,
            _clubs_by_id=MappingProxyType(by_id),
            _clubs_by_scope=MappingProxyType(
                {scope: tuple(scope_clubs) for scope, scope_clubs in by_scope.items()}
            ),
        )

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(self._clubs_by_scope)

    def club(self, club_id: str) -> Club:
        try:
            return self._clubs_by_id[club_id]
        except KeyError as error:
            raise KeyError(f"Unknown club id {club_id!r}") from error

    def for_scope(self, scope: str) -> tuple[Club, ...]:
        if scope == "all":
            return self.clubs
        try:
            return self._clubs_by_scope[scope]
        except KeyError as error:
            raise KeyError(f"Unknown club scope {scope!r}") from error


def load_packaged_data(path: Path, *, strict: bool = True) -> dict[str, Any]:
    """Read a version-one manifest and strictly validate its club records.

    ``strict=False`` is reserved for lightweight setup/readiness checks. Domain and
    gameplay callers should use the strict default or :func:`load_club_catalog`.
    """

    path = Path(path)
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ClubDataError(f"Packaged club data was not found at {path}") from error
    except OSError as error:
        raise ClubDataError(
            f"Packaged club data at {path} could not be read"
        ) from error
    except json.JSONDecodeError as error:
        raise ClubDataError(
            f"Packaged club data at {path} is not valid JSON"
        ) from error

    _validate_manifest_envelope(raw_data)
    if strict:
        _clubs_from_manifest(raw_data, path)
    return raw_data


def load_club_catalog(path: Path) -> ClubCatalog:
    """Load a manifest into a strictly validated immutable catalog."""

    path = Path(path)
    raw_data = load_packaged_data(path, strict=False)
    return ClubCatalog.from_clubs(_clubs_from_manifest(raw_data, path))


def packaged_data_is_ready(path: Path) -> bool:
    """Return whether a non-empty packaged manifest is available.

    Complete domain records are validated strictly. The small legacy ``name``-only
    probe accepted by the initial setup tests remains a valid readiness marker.
    """

    try:
        data = load_packaged_data(path, strict=False)
        if not (data["competitions"] and data["clubs"]):
            return False
        if all(
            isinstance(club, dict) and set(club) == {"name"} for club in data["clubs"]
        ):
            return True
        _clubs_from_manifest(data, Path(path))
    except ClubDataError:
        return False
    return True


def _validate_manifest_envelope(raw_data: Any) -> None:
    if not isinstance(raw_data, dict):
        raise ClubDataError("Packaged club data must be a JSON object")
    if raw_data.get("schema_version") != 1:
        raise ClubDataError("Packaged club data has an unsupported schema version")
    if not isinstance(raw_data.get("competitions"), list):
        raise ClubDataError("Packaged club data must contain a competitions list")
    if not isinstance(raw_data.get("clubs"), list):
        raise ClubDataError("Packaged club data must contain a clubs list")


def _clubs_from_manifest(
    raw_data: Mapping[str, Any], manifest_path: Path
) -> tuple[Club, ...]:
    clubs = tuple(
        _validate_club_record(record, index, manifest_path.parent)
        for index, record in enumerate(raw_data["clubs"])
    )
    # Constructing the catalog also validates unique internal ids.
    ClubCatalog.from_clubs(clubs)

    provider_ids: set[int | str] = set()
    for club in clubs:
        if club.provider_id in provider_ids:
            raise ClubDataError(f"Club provider_id {club.provider_id!r} is duplicated")
        provider_ids.add(club.provider_id)
    return clubs


def _validate_club_record(record: Any, index: int, data_root: Path) -> Club:
    label = f"clubs[{index}]"
    if not isinstance(record, dict):
        raise ClubDataError(f"{label} must be a JSON object")

    missing = sorted(_REQUIRED_CLUB_FIELDS.difference(record))
    if "crest" not in record and "crest_path" not in record:
        missing.append("crest")
    if missing:
        raise ClubDataError(f"{label} is missing required fields: {', '.join(missing)}")
    if "crest" in record and "crest_path" in record:
        raise ClubDataError(f"{label} must not contain both crest and crest_path")

    club_id = _required_text(record["id"], f"{label}.id")
    if not _CLUB_ID_PATTERN.fullmatch(club_id):
        raise ClubDataError(
            f"{label}.id must contain only lowercase letters, numbers, '-' or '_'"
        )

    provider_id = record["provider_id"]
    if isinstance(provider_id, bool) or not isinstance(provider_id, int | str):
        raise ClubDataError(f"{label}.provider_id must be an integer or string")
    if isinstance(provider_id, int) and provider_id <= 0:
        raise ClubDataError(f"{label}.provider_id must be positive")
    if isinstance(provider_id, str) and not provider_id.strip():
        raise ClubDataError(f"{label}.provider_id must not be empty")

    name = _required_text(record["name"], f"{label}.name")
    scope = _required_text(record["scope"], f"{label}.scope")
    if scope == "all" or not _SLUG_PATTERN.fullmatch(scope):
        raise ClubDataError(f"{label}.scope must be a lowercase slug other than 'all'")
    league = _required_text(record["league"], f"{label}.league")
    season = _required_text(record["season"], f"{label}.season")

    source_url = _required_text(record["source_url"], f"{label}.source_url")
    parsed_url = urlparse(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ClubDataError(f"{label}.source_url must be an absolute HTTP(S) URL")

    crest_field = "crest" if "crest" in record else "crest_path"
    crest_path = _required_text(record[crest_field], f"{label}.{crest_field}")
    _validate_crest(crest_path, data_root, f"{label}.{crest_field}")

    cover_fields = {"covered_crest", "theme_colors", "cover_regions", "cover_status"}
    present_cover_fields = cover_fields.intersection(record)
    if present_cover_fields and present_cover_fields != cover_fields:
        missing_cover_fields = ", ".join(sorted(cover_fields - present_cover_fields))
        raise ClubDataError(f"{label} is missing cover fields: {missing_cover_fields}")
    if present_cover_fields:
        covered_crest_path = _required_text(
            record["covered_crest"], f"{label}.covered_crest"
        )
        _validate_crest(covered_crest_path, data_root, f"{label}.covered_crest")
        cover_status = record["cover_status"]
        if cover_status not in {"covered", "not_required", "manual_review"}:
            raise ClubDataError(f"{label}.cover_status is invalid")
        try:
            theme_colors = validate_theme_colors(
                record["theme_colors"], label=f"{label}.theme_colors"
            )
            cover_regions = validate_cover_regions(
                record["cover_regions"], label=f"{label}.cover_regions"
            )
        except CrestCoverError as error:
            raise ClubDataError(str(error)) from error
        if cover_status == "covered" and not cover_regions:
            raise ClubDataError(f"{label} is covered but has no cover regions")
        if cover_status != "covered" and cover_regions:
            raise ClubDataError(
                f"{label} has regions but cover_status is {cover_status!r}"
            )
    else:
        # Backwards compatibility for small domain fixtures; canonical packaged data
        # validation requires explicit reviewed cover metadata.
        covered_crest_path = crest_path
        theme_colors = {"primary": "#143E34", "secondary": "#FFFFFF"}
        cover_regions = []
        cover_status = "not_required"

    return Club(
        id=club_id,
        provider_id=provider_id,
        name=name,
        scope=scope,
        league=league,
        season=season,
        source_url=source_url,
        crest_path=crest_path,
        covered_crest_path=covered_crest_path,
        theme_colors=theme_colors,
        cover_regions=cover_regions,
        cover_status=cover_status,
    )


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClubDataError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ClubDataError(
            f"{field_name} must not have leading or trailing whitespace"
        )
    return value


def _validate_crest(crest_path: str, data_root: Path, field_name: str) -> None:
    pure_path = PurePosixPath(crest_path)
    if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in crest_path:
        raise ClubDataError(f"{field_name} must be a safe relative path")
    if pure_path.suffix.lower() not in _SUPPORTED_CREST_SUFFIXES:
        raise ClubDataError(f"{field_name} has an unsupported image type")

    data_root = data_root.resolve()
    asset_path = (data_root / Path(*pure_path.parts)).resolve()
    if not asset_path.is_relative_to(data_root):
        raise ClubDataError(f"{field_name} resolves outside the data directory")
    if not asset_path.is_file():
        raise ClubDataError(f"Crest file was not found: {crest_path}")

    try:
        content = asset_path.read_bytes()
    except OSError as error:
        raise ClubDataError(f"Crest file could not be read: {crest_path}") from error
    suffix = pure_path.suffix.lower()
    if not _content_matches_suffix(content, suffix):
        raise ClubDataError(f"Crest file is not a valid image: {crest_path}")
    if suffix == ".png":
        try:
            with Image.open(asset_path) as image:
                image.load()
                if image.format != "PNG":
                    raise ClubDataError(f"Crest file is not a PNG: {crest_path}")
                if image.size != (256, 256):
                    raise ClubDataError(
                        f"Crest PNG must be 256x256 pixels: {crest_path}"
                    )
                if image.mode != "RGBA":
                    raise ClubDataError(f"Crest PNG must use RGBA mode: {crest_path}")
        except (OSError, UnidentifiedImageError) as error:
            raise ClubDataError(
                f"Crest file is not a valid PNG: {crest_path}"
            ) from error


def _content_matches_suffix(content: bytes, suffix: str) -> bool:
    if suffix == ".png":
        return len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return (
            len(content) >= 4
            and content.startswith(b"\xff\xd8\xff")
            and content.endswith(b"\xff\xd9")
        )
    if suffix == ".webp":
        return (
            len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        )
    if suffix == ".gif":
        return len(content) >= 13 and content[:6] in {b"GIF87a", b"GIF89a"}
    if suffix == ".svg":
        try:
            root = ElementTree.fromstring(content)
        except (ElementTree.ParseError, UnicodeDecodeError):
            return False
        return root.tag.rsplit("}", 1)[-1].lower() == "svg"
    return False
