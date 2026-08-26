from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.club_data import ClubDataError, load_club_catalog, load_packaged_data


def write_svg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")


def club_record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "example-fc",
        "provider_id": 42,
        "name": "Example FC",
        "scope": "premier-league",
        "league": "Premier League",
        "season": "2025/26",
        "source_url": "https://example.test/clubs/42",
        "crest_path": "crests/example-fc.svg",
    }
    record.update(changes)
    return record


def write_manifest(path: Path, clubs: list[object]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "competitions": [{"code": "PL"}],
                "clubs": clubs,
            }
        ),
        encoding="utf-8",
    )


def test_strict_loader_accepts_canonical_importer_schema(tmp_path: Path) -> None:
    manifest = tmp_path / "clubs.json"
    write_svg(tmp_path / "crests" / "opaque.svg")
    record = club_record(id="club_0123456789abcdef01234567")
    record.pop("crest_path")
    record["crest"] = "crests/opaque.svg"
    write_manifest(manifest, [record])

    loaded = load_club_catalog(manifest).clubs[0]

    assert loaded.id == "club_0123456789abcdef01234567"
    assert loaded.crest_path == "crests/opaque.svg"


def test_strict_loader_returns_typed_catalog_and_private_provider_id(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "clubs.json"
    write_svg(tmp_path / "crests/example-fc.svg")
    write_manifest(manifest, [club_record()])

    catalog = load_club_catalog(manifest)
    loaded = catalog.clubs[0]

    assert loaded.id == "example-fc"
    assert loaded.provider_id == 42
    assert loaded.league == "Premier League"
    assert catalog.for_scope("premier-league") == (loaded,)
    assert "_provider_id" not in repr(loaded)
    assert load_packaged_data(manifest, strict=True)["schema_version"] == 1


def test_strict_loader_reports_missing_schema_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "clubs.json"
    record = club_record()
    del record["season"]
    write_manifest(manifest, [record])

    with pytest.raises(ClubDataError, match=r"clubs\[0\].*season"):
        load_club_catalog(manifest)


def test_strict_loader_rejects_invalid_values_and_duplicate_ids(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "clubs.json"
    write_svg(tmp_path / "crests/example-fc.svg")
    write_manifest(manifest, [club_record(scope="Premier League")])
    with pytest.raises(ClubDataError, match="lowercase slug"):
        load_club_catalog(manifest)

    write_manifest(manifest, [club_record(), club_record(provider_id=43)])
    with pytest.raises(ClubDataError, match="duplicated"):
        load_club_catalog(manifest)


def test_strict_loader_rejects_missing_and_invalid_crest_files(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "clubs.json"
    write_manifest(manifest, [club_record()])
    with pytest.raises(ClubDataError, match="Crest file was not found"):
        load_club_catalog(manifest)

    crest = tmp_path / "crests/example-fc.svg"
    crest.parent.mkdir()
    crest.write_text("not an svg", encoding="utf-8")
    with pytest.raises(ClubDataError, match="not a valid image"):
        load_club_catalog(manifest)


def test_strict_loader_rejects_unsafe_crest_path_and_source_url(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "clubs.json"
    write_manifest(manifest, [club_record(crest_path="../outside.svg")])
    with pytest.raises(ClubDataError, match="safe relative path"):
        load_club_catalog(manifest)

    write_manifest(manifest, [club_record(source_url="example.test/club")])
    with pytest.raises(ClubDataError, match=r"HTTP\(S\)"):
        load_club_catalog(manifest)
