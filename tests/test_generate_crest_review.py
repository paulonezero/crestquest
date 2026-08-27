from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scripts.generate_crest_review import (
    generate_review_page,
    visible_obscured_fraction,
)


def _write_image(path: Path, pixels: list[tuple[int, int, int, int]]) -> None:
    image = Image.new("RGBA", (2, 2))
    image.putdata(pixels)
    image.save(path, format="PNG")


def test_visible_obscured_fraction_only_counts_visible_original_pixels(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.png"
    covered = tmp_path / "covered.png"
    transparent = (0, 0, 0, 0)
    visible = (20, 40, 60, 255)
    changed = (200, 100, 50, 255)
    _write_image(original, [visible, visible, transparent, transparent])
    _write_image(covered, [changed, visible, changed, transparent])

    assert visible_obscured_fraction(original, covered) == 0.5


def test_generate_review_page_embeds_records_and_export_controls(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    (data / "crests").mkdir(parents=True)
    (data / "covered-crests").mkdir()
    original = data / "crests" / "original.png"
    covered = data / "covered-crests" / "covered.png"
    visible = (20, 40, 60, 255)
    changed = (200, 100, 50, 255)
    _write_image(original, [visible, visible, visible, visible])
    _write_image(covered, [changed, changed, visible, visible])
    manifest = data / "clubs.json"
    manifest.write_text(
        json.dumps(
            {
                "clubs": [
                    {
                        "provider_id": 123,
                        "name": "Example FC",
                        "league": "Example League",
                        "crest": "crests/original.png",
                        "covered_crest": "covered-crests/covered.png",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "var" / "crest-review" / "index.html"

    generated, records = generate_review_page(manifest, output)

    assert generated == output
    assert records[0]["coverage_percent"] == 50.0
    page = output.read_text(encoding="utf-8")
    assert "Example FC" in page
    assert "Copy selected list" in page
    assert "Download JSON" in page
    assert ".join(String.fromCharCode(10))" in page
    assert "crestquest.cover-review.selection.v1" in page
