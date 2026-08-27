from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.prepare_crest_assets import prepare_crest_assets
from src.crest_covering import (
    CrestCoverError,
    crest_image_sha256,
    extract_theme_colors,
    generate_covered_crest,
    validate_cover_regions,
)


def _rgb(color: str) -> tuple[int, int, int]:
    return (
        int(color[1:3], 16),
        int(color[3:5], 16),
        int(color[5:7], 16),
    )


def test_theme_extraction_ignores_transparency_and_is_deterministic() -> None:
    image = Image.new("RGBA", (20, 20), (240, 10, 10, 0))
    for x in range(4, 16):
        for y in range(4, 16):
            image.putpixel((x, y), (20, 80, 160, 255))
    for x in range(8, 12):
        for y in range(8, 12):
            image.putpixel((x, y), (245, 245, 245, 255))

    first = extract_theme_colors(image)
    second = extract_theme_colors(image.copy())

    assert first == second
    primary = _rgb(first["primary"])
    assert primary[2] > primary[1] > primary[0]
    assert first["primary"] != first["secondary"]


def test_theme_extraction_handles_mostly_white_and_mostly_black_crests() -> None:
    white = Image.new("RGBA", (20, 20), "white")
    black = Image.new("RGBA", (20, 20), "black")
    for x in range(8, 12):
        for y in range(8, 12):
            white.putpixel((x, y), (10, 60, 170, 255))
            black.putpixel((x, y), (220, 190, 20, 255))

    white_theme = extract_theme_colors(white)
    black_theme = extract_theme_colors(black)

    assert white_theme["primary"] != white_theme["secondary"]
    assert black_theme["primary"] != black_theme["secondary"]
    assert (
        max(_rgb(white_theme["secondary"])) - min(_rgb(white_theme["secondary"])) < 20
    )
    assert (
        max(_rgb(black_theme["secondary"])) - min(_rgb(black_theme["secondary"])) < 20
    )


def test_manual_theme_override_is_stored_exactly() -> None:
    image = Image.new("RGBA", (8, 8), (20, 80, 160, 255))

    colors = extract_theme_colors(
        image,
        {"primary": "#c8102e", "secondary": "#ffffff"},
    )

    assert colors == {"primary": "#C8102E", "secondary": "#FFFFFF"}


def test_normalized_rectangles_polygons_and_multiple_regions_validate() -> None:
    regions = validate_cover_regions(
        [
            {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.1},
            {
                "shape": "polygon",
                "points": [
                    {"x": 0.2, "y": 0.6},
                    {"x": 0.8, "y": 0.6},
                    {"x": 0.5, "y": 0.8},
                ],
            },
        ]
    )

    assert len(regions) == 2
    assert regions[0]["shape"] == "rounded_rectangle"
    assert regions[1]["shape"] == "polygon"


@pytest.mark.parametrize(
    "region",
    [
        {"x": -0.1, "y": 0.2, "width": 0.5, "height": 0.1},
        {"x": 0.8, "y": 0.2, "width": 0.3, "height": 0.1},
        {"x": 0.1, "y": 0.2, "width": 0.0, "height": 0.1},
        {
            "shape": "polygon",
            "points": [
                {"x": 0.1, "y": 0.1},
                {"x": 1.1, "y": 0.2},
                {"x": 0.2, "y": 0.3},
            ],
        },
    ],
)
def test_malformed_or_out_of_bounds_regions_are_rejected(
    region: dict[str, object],
) -> None:
    with pytest.raises(CrestCoverError):
        validate_cover_regions([region])


def test_multiple_regions_generate_an_opaque_cover_and_preserve_original() -> None:
    original = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    for x in range(30, 226):
        for y in range(20, 236):
            original.putpixel((x, y), (20, 80, 160, 255))
    before = hashlib.sha256(original.tobytes()).digest()
    regions = [
        {
            "x": 0.1,
            "y": 0.15,
            "width": 0.5,
            "height": 0.12,
            "shape": "rounded_rectangle",
        },
        {
            "shape": "polygon",
            "points": [
                {"x": 0.2, "y": 0.65},
                {"x": 0.8, "y": 0.65},
                {"x": 0.7, "y": 0.8},
                {"x": 0.3, "y": 0.8},
            ],
        },
    ]

    covered = generate_covered_crest(
        original,
        regions,
        {"primary": "#C8102E", "secondary": "#FFFFFF"},
    )

    assert covered.size == (256, 256)
    assert covered.mode == "RGBA"
    assert hashlib.sha256(original.tobytes()).digest() == before
    alpha = covered.getchannel("A")
    assert alpha.getpixel((70, 50)) == 255
    assert alpha.getpixel((128, 180)) == 255
    assert covered.getpixel((0, 0)) == (0, 0, 0, 0)
    assert covered.getpixel((128, 128)) == original.getpixel((128, 128))


def test_club_not_requiring_cover_is_a_pixel_identical_copy() -> None:
    original = Image.new("RGBA", (64, 64), (30, 120, 60, 0))
    for x in range(10, 54):
        for y in range(10, 54):
            original.putpixel((x, y), (30, 120, 60, 255))

    covered = generate_covered_crest(
        original,
        [],
        {"primary": "#1E783C", "secondary": "#FFFFFF"},
    )

    assert covered.tobytes() == original.tobytes()
    assert covered is not original


def test_preparation_stores_manual_override_and_preserves_original_file(
    tmp_path: Path,
) -> None:
    crest_dir = tmp_path / "crests"
    crest_dir.mkdir()
    original_path = crest_dir / "opaque.png"
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    for x in range(20, 236):
        for y in range(20, 236):
            image.putpixel((x, y), (20, 80, 160, 255))
    image.save(original_path, format="PNG")
    original_digest = hashlib.sha256(original_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "clubs.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clubs": [
                    {
                        "provider_id": 123,
                        "name": "Example FC",
                        "crest": "crests/opaque.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clubs": [
                    {
                        "provider_team_id": 123,
                        "review_status": "covered",
                        "theme_colors": {
                            "primary": "#C8102E",
                            "secondary": "#FFFFFF",
                        },
                        "cover_regions": [
                            {
                                "x": 0.2,
                                "y": 0.65,
                                "width": 0.6,
                                "height": 0.15,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = prepare_crest_assets(manifest_path, metadata_path)
    prepared = json.loads(manifest_path.read_text(encoding="utf-8"))["clubs"][0]

    assert summary == {
        "clubs": 1,
        "covered": 1,
        "not_required": 0,
        "manual_review": 0,
    }
    assert prepared["theme_colors"] == {
        "primary": "#C8102E",
        "secondary": "#FFFFFF",
    }
    assert prepared["covered_crest"] != prepared["crest"]
    assert (tmp_path / prepared["covered_crest"]).is_file()
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == original_digest


def test_preparation_rejects_a_crest_changed_since_schema_v2_review(
    tmp_path: Path,
) -> None:
    crest_dir = tmp_path / "crests"
    crest_dir.mkdir()
    original_path = crest_dir / "opaque.png"
    image = Image.new("RGBA", (256, 256), (20, 80, 160, 255))
    image.save(original_path, format="PNG")
    manifest_path = tmp_path / "clubs.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clubs": [
                    {
                        "provider_id": 123,
                        "name": "Changed FC",
                        "crest": "crests/opaque.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "clubs": [
                    {
                        "provider_team_id": 123,
                        "review_status": "not_required",
                        "coverage_confidence": "high",
                        "reviewed_at": "2026-08-27",
                        "reviewed_crest_sha256": "0" * 64,
                        "cover_regions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        CrestCoverError, match="changed since its crest cover was reviewed"
    ):
        prepare_crest_assets(manifest_path, metadata_path)

    assert crest_image_sha256(image) != "0" * 64
