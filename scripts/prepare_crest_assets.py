from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crest_covering import (
    CrestCoverError,
    covered_crest_path,
    extract_theme_colors,
    load_cover_metadata,
    save_covered_crest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "clubs.json"
DEFAULT_METADATA = PROJECT_ROOT / "data" / "crest_cover_metadata.json"


def prepare_crest_assets(
    manifest_path: Path | str = DEFAULT_MANIFEST,
    metadata_path: Path | str = DEFAULT_METADATA,
) -> dict[str, int]:
    """Generate covered assets and add validated runtime metadata to a manifest."""
    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CrestCoverError(f"Club manifest was not found at {path}") from error
    except OSError as error:
        raise CrestCoverError(f"Club manifest could not be read at {path}") from error
    except json.JSONDecodeError as error:
        raise CrestCoverError(f"Club manifest is not valid JSON: {path}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("clubs"), list):
        raise CrestCoverError("Club manifest must contain a clubs list")

    metadata = load_cover_metadata(metadata_path)
    records: list[dict[str, Any]] = []
    manifest_provider_ids: set[int] = set()
    for index, raw_record in enumerate(manifest["clubs"]):
        if not isinstance(raw_record, dict):
            raise CrestCoverError(f"clubs[{index}] must be an object")
        provider_id = raw_record.get("provider_id")
        if (
            isinstance(provider_id, bool)
            or not isinstance(provider_id, int)
            or provider_id <= 0
        ):
            raise CrestCoverError(
                f"clubs[{index}].provider_id must be a positive integer"
            )
        if provider_id in manifest_provider_ids:
            raise CrestCoverError(
                f"provider_id {provider_id} is duplicated in the manifest"
            )
        manifest_provider_ids.add(provider_id)
        if provider_id not in metadata:
            club_label = raw_record.get("name", provider_id)
            raise CrestCoverError(
                f"Club {club_label!r} ({provider_id}) has no "
                "crest-cover review metadata"
            )
        records.append(raw_record)

    extras = sorted(set(metadata) - manifest_provider_ids)
    if extras:
        raise CrestCoverError(
            "Crest-cover metadata contains provider IDs absent from the manifest: "
            + ", ".join(map(str, extras))
        )

    data_root = path.parent.resolve()
    with tempfile.TemporaryDirectory(
        prefix=".crestquest-covers-", dir=data_root
    ) as temporary:
        stage_root = Path(temporary)
        for index, record in enumerate(records):
            provider_id = record["provider_id"]
            annotation = metadata[provider_id]
            crest = record.get("crest", record.get("crest_path"))
            if not isinstance(crest, str) or not crest:
                raise CrestCoverError(f"clubs[{index}] has no original crest path")
            original_path = (data_root / crest).resolve()
            if (
                not original_path.is_relative_to(data_root)
                or not original_path.is_file()
            ):
                raise CrestCoverError(
                    f"Original crest is unavailable for provider ID {provider_id}"
                )

            with Image.open(original_path) as opened:
                opened.load()
                theme_colors = extract_theme_colors(
                    opened, annotation.get("theme_colors")
                )
            covered_path = covered_crest_path(provider_id)
            save_covered_crest(
                original_path,
                stage_root / covered_path,
                annotation["cover_regions"],
                theme_colors,
            )
            record["theme_colors"] = theme_colors
            record["cover_status"] = annotation["review_status"]
            record["cover_regions"] = annotation["cover_regions"]
            record["covered_crest"] = covered_path

        for generated in (stage_root / "covered-crests").glob("*.png"):
            destination = data_root / "covered-crests" / generated.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(generated, destination)

        manifest_bytes = (
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        ).encode()
        temporary_manifest = data_root / f".{path.name}.{os.getpid()}.tmp"
        try:
            temporary_manifest.write_bytes(manifest_bytes)
            os.replace(temporary_manifest, path)
        finally:
            temporary_manifest.unlink(missing_ok=True)

    expected = {record["covered_crest"] for record in records}
    covered_dir = data_root / "covered-crests"
    if covered_dir.is_dir():
        for existing in covered_dir.glob("*.png"):
            relative = existing.relative_to(data_root).as_posix()
            if relative not in expected:
                existing.unlink()

    return {
        "clubs": len(records),
        "covered": sum(record["cover_status"] == "covered" for record in records),
        "not_required": sum(
            record["cover_status"] == "not_required" for record in records
        ),
        "manual_review": sum(
            record["cover_status"] == "manual_review" for record in records
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract crest theme colours and generate covered crest assets."
    )
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = prepare_crest_assets(args.manifest, args.metadata)
    except (CrestCoverError, OSError) as error:
        print(f"Crest preparation failed: {error}", file=sys.stderr)
        return 1
    print(
        "Crest preparation passed: "
        f"{summary['clubs']} clubs; {summary['covered']} covered; "
        f"{summary['not_required']} not required; "
        f"{summary['manual_review']} awaiting manual review."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
