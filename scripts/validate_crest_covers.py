from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prepare_crest_assets import DEFAULT_MANIFEST, DEFAULT_METADATA
from src.crest_covering import (
    CrestCoverError,
    crest_image_sha256,
    load_cover_metadata,
    region_area,
)

SUSPICIOUS_REGION_AREA = 0.35


def build_cover_report(
    manifest_path: Path | str = DEFAULT_MANIFEST,
    metadata_path: Path | str = DEFAULT_METADATA,
) -> dict[str, Any]:
    path = Path(manifest_path)
    invalid: list[str] = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "active": [],
            "not_required": [],
            "manual_review": [],
            "missing": [],
            "invalid": [f"Could not read manifest: {error}"],
            "suspicious": [],
            "stale": [],
            "confidence": {},
        }
    try:
        metadata = load_cover_metadata(metadata_path)
    except CrestCoverError as error:
        return {
            "active": [],
            "not_required": [],
            "manual_review": [],
            "missing": [],
            "invalid": [str(error)],
            "suspicious": [],
            "stale": [],
            "confidence": {},
        }

    clubs = manifest.get("clubs") if isinstance(manifest, dict) else None
    if not isinstance(clubs, list):
        return {
            "active": [],
            "not_required": [],
            "manual_review": [],
            "missing": [],
            "invalid": ["Manifest must contain a clubs list"],
            "suspicious": [],
            "stale": [],
            "confidence": {},
        }

    active: list[str] = []
    not_required: list[str] = []
    manual_review: list[str] = []
    missing: list[str] = []
    suspicious: list[str] = []
    stale: list[str] = []
    confidence: dict[str, list[str]] = {
        "high": [],
        "medium": [],
        "low": [],
        "unreviewed": [],
        "legacy_unknown": [],
    }
    manifest_ids: set[int] = set()
    for index, club in enumerate(clubs):
        if not isinstance(club, dict):
            invalid.append(f"clubs[{index}] is not an object")
            continue
        provider_id = club.get("provider_id")
        name = club.get("name")
        if not isinstance(provider_id, int) or isinstance(provider_id, bool):
            invalid.append(f"clubs[{index}] has an invalid provider_id")
            continue
        manifest_ids.add(provider_id)
        label = f"{name} (provider {provider_id})"
        annotation = metadata.get(provider_id)
        if annotation is None:
            missing.append(label)
            continue
        status = annotation["review_status"]
        if status == "covered":
            active.append(label)
        elif status == "not_required":
            not_required.append(label)
        else:
            manual_review.append(label)

        confidence_value = annotation.get("coverage_confidence", "legacy_unknown")
        confidence[confidence_value].append(label)
        reviewed_digest = annotation.get("reviewed_crest_sha256")
        crest_path = club.get("crest")
        if reviewed_digest is not None and isinstance(crest_path, str):
            original_path = path.parent / crest_path
            try:
                with Image.open(original_path) as opened:
                    opened.load()
                    if crest_image_sha256(opened) != reviewed_digest:
                        stale.append(label)
            except OSError:
                # Asset validation reports unreadable originals separately.
                pass

        if club.get("cover_status") != status:
            invalid.append(f"{label}: manifest cover_status does not match metadata")
        if club.get("cover_regions") != annotation["cover_regions"]:
            invalid.append(f"{label}: manifest cover_regions do not match metadata")
        if not isinstance(club.get("theme_colors"), dict):
            invalid.append(f"{label}: generated theme colours are missing")
        covered_crest = club.get("covered_crest")
        if (
            not isinstance(covered_crest, str)
            or not (path.parent / covered_crest).is_file()
        ):
            invalid.append(f"{label}: covered asset is missing")

        for region_index, region in enumerate(annotation["cover_regions"]):
            area = region_area(region)
            if area >= SUSPICIOUS_REGION_AREA:
                suspicious.append(
                    f"{label}: region {region_index + 1} covers "
                    f"{area:.1%} of the canvas"
                )

    for provider_id in sorted(set(metadata) - manifest_ids):
        invalid.append(
            f"Metadata provider {provider_id} does not exist in the packaged manifest"
        )

    return {
        "active": sorted(active),
        "not_required": sorted(not_required),
        "manual_review": sorted(manual_review),
        "missing": sorted(missing),
        "invalid": sorted(invalid),
        "suspicious": sorted(suspicious),
        "stale": sorted(stale),
        "confidence": {
            key: sorted(entries) for key, entries in confidence.items() if entries
        },
    }


def _print_section(title: str, entries: list[str]) -> None:
    print(f"\n{title} ({len(entries)}):")
    if entries:
        for entry in entries:
            print(f"  - {entry}")
    else:
        print("  - None")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report reviewed, missing, invalid, and suspicious "
            "crest-cover regions."
        )
    )
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_cover_report(args.manifest, args.metadata)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("Crest Quest cover-region validation report")
        _print_section("Clubs with active cover regions", report["active"])
        _print_section("Clubs not requiring a cover", report["not_required"])
        _print_section("Clubs requiring manual review", report["manual_review"])
        _print_section("Missing annotations", report["missing"])
        _print_section("Invalid metadata or assets", report["invalid"])
        _print_section("Crests changed since review", report["stale"])
        _print_section("Suspiciously large regions", report["suspicious"])
        print("\nCoverage confidence:")
        for confidence, entries in report["confidence"].items():
            print(f"  - {confidence}: {len(entries)}")
    return 1 if report["missing"] or report["invalid"] or report["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
