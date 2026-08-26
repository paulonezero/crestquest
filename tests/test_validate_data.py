from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validate_data import (  # noqa: E402
    EXPECTED_COMPETITIONS,
    main,
    validate_data,
)


def _write_valid_data(data_dir: Path) -> Path:
    crest_dir = data_dir / "crests"
    covered_crest_dir = data_dir / "covered-crests"
    crest_dir.mkdir(parents=True)
    covered_crest_dir.mkdir(parents=True)
    competitions = []
    clubs = []
    for index, (scope, (name, code)) in enumerate(
        EXPECTED_COMPETITIONS.items(), start=1
    ):
        season = "2025/2026"
        crest = f"crests/{index:032x}.png"
        covered_crest = f"covered-crests/{index:032x}.png"
        image = Image.new("RGBA", (256, 256), (index, 20, 30, 0))
        image.save(data_dir / crest, format="PNG")
        image.save(data_dir / covered_crest, format="PNG")
        competitions.append(
            {"scope": scope, "name": name, "code": code, "season": season}
        )
        clubs.append(
            {
                "id": f"club_{index:024x}",
                "provider_id": 1000 + index,
                "name": f"Club {index}",
                "scope": scope,
                "league": name,
                "season": season,
                "source_url": f"https://assets.test/{index}.svg",
                "crest": crest,
                "covered_crest": covered_crest,
                "theme_colors": {
                    "primary": "#1450A0",
                    "secondary": "#FFFFFF",
                },
                "cover_status": "not_required",
                "cover_regions": [],
            }
        )
    manifest = data_dir / "clubs.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "competitions": competitions,
                "clubs": clubs,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _read_manifest(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_validates_complete_eight_competition_dataset(tmp_path: Path) -> None:
    manifest = _write_valid_data(tmp_path)

    assert validate_data(manifest) == []


def test_reports_missing_and_invalid_png_assets(tmp_path: Path) -> None:
    manifest_path = _write_valid_data(tmp_path)
    manifest = _read_manifest(manifest_path)
    clubs = manifest["clubs"]
    (tmp_path / clubs[0]["crest"]).unlink()
    Image.new("RGB", (128, 256), "red").save(tmp_path / clubs[1]["crest"], format="PNG")

    errors = validate_data(manifest_path)

    assert any("asset is missing" in error for error in errors)
    assert any("must be 256x256" in error for error in errors)
    assert any("must use RGBA mode" in error for error in errors)


def test_reports_duplicate_ids_provider_ids_and_assets(tmp_path: Path) -> None:
    manifest_path = _write_valid_data(tmp_path)
    manifest = _read_manifest(manifest_path)
    first, second = manifest["clubs"][:2]
    second["id"] = first["id"]
    second["provider_id"] = first["provider_id"]
    second["crest"] = first["crest"]
    _write_manifest(manifest_path, manifest)

    errors = validate_data(manifest_path)

    assert any("duplicate club id" in error for error in errors)
    assert any("duplicate club provider_id" in error for error in errors)
    assert any("duplicate club crest" in error for error in errors)
    assert any("unreferenced crest asset" in error for error in errors)


def test_requires_exact_supported_competition_set(tmp_path: Path) -> None:
    manifest_path = _write_valid_data(tmp_path)
    manifest = _read_manifest(manifest_path)
    manifest["competitions"].pop()
    _write_manifest(manifest_path, manifest)

    errors = validate_data(manifest_path)

    assert any("exactly 7 entries" in error for error in errors)
    assert any("missing competition scope: eredivisie" in error for error in errors)
    assert any("has no matching competition" in error for error in errors)


def test_reports_bad_json_shapes_without_crashing(tmp_path: Path) -> None:
    manifest = tmp_path / "clubs.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "not-a-date",
                "competitions": [{"scope": {}, "name": 3, "code": [], "season": None}],
                "clubs": [{"scope": []}],
            }
        ),
        encoding="utf-8",
    )

    errors = validate_data(manifest)

    assert errors
    assert any("generated_at" in error for error in errors)
    assert any("must be a non-empty string" in error for error in errors)


def test_cli_returns_nonzero_and_prints_all_errors(tmp_path: Path, capsys) -> None:
    manifest = _write_valid_data(tmp_path)
    (tmp_path / "crests" / f"{1:032x}.png").unlink()

    return_code = main([str(manifest)])

    captured = capsys.readouterr()
    assert return_code == 1
    assert "Validation failed" in captured.err
    assert "asset is missing" in captured.err
