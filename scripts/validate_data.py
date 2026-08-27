from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.crest_covering import (  # noqa: E402
    CrestCoverError,
    validate_cover_regions,
    validate_theme_colors,
)

DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "clubs.json"
SCHEMA_VERSION = 1
EXPECTED_COMPETITIONS = {
    "premier-league": ("Premier League", "PL"),
    "bundesliga": ("Bundesliga", "BL1"),
    "la-liga": ("La Liga", "PD"),
    "primeira-liga": ("Primeira Liga", "PPL"),
    "ligue-1": ("Ligue 1", "FL1"),
    "serie-a": ("Serie A", "SA"),
    "eredivisie": ("Eredivisie", "DED"),
    "championship": ("Championship", "ELC"),
}
CLUB_FIELDS = {
    "id",
    "provider_id",
    "name",
    "scope",
    "league",
    "season",
    "source_url",
    "crest",
    "covered_crest",
    "theme_colors",
    "cover_regions",
    "cover_status",
}
COMPETITION_FIELDS = {"scope", "name", "code", "season"}
CLUB_ID_PATTERN = re.compile(r"club_[0-9a-f]{24}\Z")
CREST_PATH_PATTERN = re.compile(r"crests/[0-9a-f]{32}\.png\Z")
COVERED_CREST_PATH_PATTERN = re.compile(r"covered-crests/[0-9a-f]{32}\.png\Z")


def _duplicates(values: list[Any]) -> list[Any]:
    hashable_values = []
    for value in values:
        try:
            hash(value)
        except TypeError:
            continue
        hashable_values.append(value)
    return sorted(
        (value for value, count in Counter(hashable_values).items() if count > 1),
        key=repr,
    )


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_http_url(value: Any) -> bool:
    if not _is_non_empty_string(value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validate_generated_at(value: Any, errors: list[str]) -> None:
    if not _is_non_empty_string(value):
        errors.append("generated_at must be a non-empty ISO-8601 timestamp")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"generated_at is not a valid ISO-8601 timestamp: {value!r}")


def _validate_competitions(
    competitions: Any, errors: list[str]
) -> dict[str, dict[str, Any]]:
    if not isinstance(competitions, list):
        errors.append("competitions must be a list")
        return {}

    if len(competitions) != len(EXPECTED_COMPETITIONS):
        errors.append(
            "competitions must contain exactly "
            f"{len(EXPECTED_COMPETITIONS)} entries; found {len(competitions)}"
        )

    valid_entries: list[dict[str, Any]] = []
    for index, competition in enumerate(competitions):
        label = f"competitions[{index}]"
        if not isinstance(competition, dict):
            errors.append(f"{label} must be an object")
            continue
        missing = sorted(COMPETITION_FIELDS - competition.keys())
        if missing:
            errors.append(f"{label} is missing fields: {', '.join(missing)}")
        for field in COMPETITION_FIELDS:
            if field in competition and not _is_non_empty_string(competition[field]):
                errors.append(f"{label}.{field} must be a non-empty string")
        valid_entries.append(competition)

    scopes = [entry.get("scope") for entry in valid_entries]
    codes = [entry.get("code") for entry in valid_entries]
    for duplicate in _duplicates(scopes):
        errors.append(f"duplicate competition scope: {duplicate!r}")
    for duplicate in _duplicates(codes):
        errors.append(f"duplicate competition code: {duplicate!r}")

    actual_scopes = {scope for scope in scopes if isinstance(scope, str)}
    expected_scopes = set(EXPECTED_COMPETITIONS)
    for scope in sorted(expected_scopes - actual_scopes):
        errors.append(f"missing competition scope: {scope}")
    for scope in sorted(actual_scopes - expected_scopes):
        errors.append(f"unsupported competition scope: {scope}")

    by_scope: dict[str, dict[str, Any]] = {}
    for entry in valid_entries:
        scope = entry.get("scope")
        if (
            not isinstance(scope, str)
            or scope not in EXPECTED_COMPETITIONS
            or scope in by_scope
        ):
            continue
        by_scope[scope] = entry
        expected_name, expected_code = EXPECTED_COMPETITIONS[scope]
        if entry.get("name") != expected_name:
            errors.append(
                f"competition {scope!r} name must be {expected_name!r}; "
                f"found {entry.get('name')!r}"
            )
        if entry.get("code") != expected_code:
            errors.append(
                f"competition {scope!r} code must be {expected_code!r}; "
                f"found {entry.get('code')!r}"
            )
    return by_scope


def _validate_club_fields(
    club: dict[str, Any],
    index: int,
    competitions: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    label = f"clubs[{index}]"
    missing = sorted(CLUB_FIELDS - club.keys())
    if missing:
        errors.append(f"{label} is missing fields: {', '.join(missing)}")

    for field in (
        "id",
        "name",
        "scope",
        "league",
        "season",
        "crest",
        "covered_crest",
        "cover_status",
    ):
        if field in club and not _is_non_empty_string(club[field]):
            errors.append(f"{label}.{field} must be a non-empty string")

    club_id = club.get("id")
    if (
        isinstance(club_id, str)
        and club_id.strip()
        and not CLUB_ID_PATTERN.fullmatch(club_id)
    ):
        errors.append(f"{label}.id is not an opaque Crest Quest club ID: {club_id!r}")

    provider_id = club.get("provider_id")
    if (
        not isinstance(provider_id, int)
        or isinstance(provider_id, bool)
        or provider_id <= 0
    ):
        errors.append(f"{label}.provider_id must be a positive integer")

    if not _is_http_url(club.get("source_url")):
        errors.append(f"{label}.source_url must be an http(s) URL")

    crest = club.get("crest")
    if (
        isinstance(crest, str)
        and crest.strip()
        and not CREST_PATH_PATTERN.fullmatch(crest)
    ):
        errors.append(
            f"{label}.crest must match crests/<32-character opaque hash>.png; "
            f"found {crest!r}"
        )

    covered_crest = club.get("covered_crest")
    if (
        isinstance(covered_crest, str)
        and covered_crest.strip()
        and not COVERED_CREST_PATH_PATTERN.fullmatch(covered_crest)
    ):
        errors.append(
            f"{label}.covered_crest must match "
            "covered-crests/<32-character opaque hash>.png; "
            f"found {covered_crest!r}"
        )
    if crest == covered_crest and isinstance(crest, str):
        errors.append(f"{label}.covered_crest must be separate from the original crest")

    cover_status = club.get("cover_status")
    if cover_status not in {"covered", "not_required", "manual_review"}:
        errors.append(f"{label}.cover_status is invalid: {cover_status!r}")
    try:
        validate_theme_colors(club.get("theme_colors"), label=f"{label}.theme_colors")
        regions = validate_cover_regions(
            club.get("cover_regions"), label=f"{label}.cover_regions"
        )
    except CrestCoverError as error:
        errors.append(str(error))
        regions = []
    if cover_status == "covered" and not regions:
        errors.append(f"{label} is covered but has no cover regions")
    if cover_status in {"not_required", "manual_review"} and regions:
        errors.append(f"{label} has regions but cover_status is {cover_status!r}")

    scope = club.get("scope")
    competition = competitions.get(scope) if isinstance(scope, str) else None
    if competition is None:
        if _is_non_empty_string(scope):
            errors.append(f"{label}.scope has no matching competition: {scope!r}")
        return
    if club.get("league") != competition.get("name"):
        errors.append(
            f"{label}.league does not match competition {scope!r}: "
            f"{club.get('league')!r}"
        )
    if club.get("season") != competition.get("season"):
        errors.append(
            f"{label}.season does not match competition {scope!r}: "
            f"{club.get('season')!r}"
        )


def _validate_png(path: Path, label: str, errors: list[str]) -> None:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        errors.append("Pillow is required to validate crest images")
        return

    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                errors.append(f"{label} is not a PNG image: {path}")
            if image.size != (256, 256):
                errors.append(
                    f"{label} must be 256x256 pixels; found "
                    f"{image.width}x{image.height}: {path}"
                )
            if image.mode != "RGBA":
                errors.append(
                    f"{label} must use RGBA mode; found {image.mode!r}: {path}"
                )
    except (OSError, UnidentifiedImageError) as exc:
        errors.append(f"{label} is not a valid PNG: {path} ({exc})")


def _validate_clubs(
    clubs: Any,
    competitions: dict[str, dict[str, Any]],
    manifest_path: Path,
    errors: list[str],
) -> None:
    if not isinstance(clubs, list):
        errors.append("clubs must be a list")
        return
    if not clubs:
        errors.append("clubs must contain at least one club")
        return

    valid_clubs: list[tuple[int, dict[str, Any]]] = []
    for index, club in enumerate(clubs):
        if not isinstance(club, dict):
            errors.append(f"clubs[{index}] must be an object")
            continue
        _validate_club_fields(club, index, competitions, errors)
        valid_clubs.append((index, club))

    unique_fields = ("id", "provider_id", "crest", "covered_crest")
    for field in unique_fields:
        values = [
            club.get(field) for _, club in valid_clubs if club.get(field) is not None
        ]
        for duplicate in _duplicates(values):
            errors.append(f"duplicate club {field}: {duplicate!r}")

    scoped_names = [
        (club.get("scope"), club.get("name"))
        for _, club in valid_clubs
        if club.get("scope") is not None and club.get("name") is not None
    ]
    for scope, name in _duplicates(scoped_names):
        errors.append(f"duplicate club name in scope {scope!r}: {name!r}")

    represented_scopes = {
        scope
        for _, club in valid_clubs
        if isinstance((scope := club.get("scope")), str) and scope in competitions
    }
    for scope in sorted(set(competitions) - represented_scopes):
        errors.append(f"competition scope has no clubs: {scope}")

    data_dir = manifest_path.parent.resolve()
    referenced_paths: set[Path] = set()
    for index, club in valid_clubs:
        for field, pattern in (
            ("crest", CREST_PATH_PATTERN),
            ("covered_crest", COVERED_CREST_PATH_PATTERN),
        ):
            asset = club.get(field)
            if (
                not isinstance(asset, str)
                or not asset.strip()
                or not pattern.fullmatch(asset)
            ):
                continue
            path = (manifest_path.parent / asset).resolve()
            try:
                path.relative_to(data_dir)
            except ValueError:
                errors.append(
                    f"clubs[{index}].{field} escapes the data directory: {asset!r}"
                )
                continue
            if path in referenced_paths:
                continue
            referenced_paths.add(path)
            if not path.is_file():
                errors.append(f"clubs[{index}].{field} asset is missing: {asset}")
                continue
            _validate_png(path, f"clubs[{index}].{field}", errors)

    for directory_name in ("crests", "covered-crests"):
        asset_dir = manifest_path.parent / directory_name
        if not asset_dir.is_dir():
            continue
        actual_paths = {path.resolve() for path in asset_dir.glob("*.png")}
        for orphan in sorted(actual_paths - referenced_paths):
            errors.append(
                "unreferenced crest asset: "
                f"{orphan.relative_to(manifest_path.parent.resolve())}"
            )


def validate_data(manifest_path: Path | str = DEFAULT_MANIFEST) -> list[str]:
    """Return all validation errors for a Crest Quest data manifest and its assets."""
    path = Path(manifest_path)
    errors: list[str] = []
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"manifest does not exist: {path}"]
    except OSError as exc:
        return [f"could not read manifest {path}: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"manifest is not valid JSON: {path} ({exc})"]

    if not isinstance(raw_data, dict):
        return ["manifest root must be a JSON object"]
    if raw_data.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION}; "
            f"found {raw_data.get('schema_version')!r}"
        )
    _validate_generated_at(raw_data.get("generated_at"), errors)
    competitions = _validate_competitions(raw_data.get("competitions"), errors)
    _validate_clubs(raw_data.get("clubs"), competitions, path, errors)
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the Crest Quest club manifest and normalized crest assets."
        )
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"manifest to validate (default: {DEFAULT_MANIFEST})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_data(args.manifest)
    if errors:
        print(
            f"Validation failed with {len(errors)} error(s):",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Validation passed: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
