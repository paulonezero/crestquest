from __future__ import annotations

from pathlib import Path

import pytest

from src.club_data import ClubDataError, load_packaged_data, packaged_data_is_ready


def test_empty_manifest_is_valid_but_not_ready(tmp_path: Path) -> None:
    path = tmp_path / "clubs.json"
    path.write_text(
        '{"schema_version": 1, "competitions": [], "clubs": []}',
        encoding="utf-8",
    )

    assert load_packaged_data(path)["clubs"] == []
    assert packaged_data_is_ready(path) is False


def test_manifest_with_competitions_and_clubs_is_ready(tmp_path: Path) -> None:
    path = tmp_path / "clubs.json"
    path.write_text(
        """{
          "schema_version": 1,
          "competitions": [{"code": "PL"}],
          "clubs": [{"name": "Example FC"}]
        }""",
        encoding="utf-8",
    )

    assert packaged_data_is_ready(path) is True


def test_invalid_manifest_has_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / "clubs.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ClubDataError, match="must be a JSON object"):
        load_packaged_data(path)
