from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_REVIEW_STATUSES = frozenset({"covered", "not_required", "manual_review"})
_COVERAGE_CONFIDENCES = frozenset({"high", "medium", "low", "unreviewed"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_REVIEWED_AT_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_RECTANGLE_SHAPE = "rounded_rectangle"
_POLYGON_SHAPE = "polygon"


class CrestCoverError(ValueError):
    """Raised when crest-cover metadata or image preparation is invalid."""


def covered_crest_path(provider_team_id: int) -> str:
    digest = hashlib.sha256(
        f"crest-quest:football-data:covered-crest:v1:{provider_team_id}".encode()
    ).hexdigest()
    return f"covered-crests/{digest[:32]}.png"


def crest_image_sha256(image: Image.Image) -> str:
    """Return a stable digest of the reviewed crest pixels and dimensions."""
    rgba = image.convert("RGBA")
    digest = hashlib.sha256()
    digest.update(f"{rgba.width}x{rgba.height}:RGBA\0".encode())
    digest.update(rgba.tobytes())
    return digest.hexdigest()


def load_cover_metadata(path: Path | str) -> dict[int, dict[str, Any]]:
    metadata_path = Path(path)
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CrestCoverError(
            f"Crest-cover metadata was not found at {metadata_path}"
        ) from error
    except OSError as error:
        raise CrestCoverError(
            f"Crest-cover metadata could not be read at {metadata_path}"
        ) from error
    except json.JSONDecodeError as error:
        raise CrestCoverError(
            f"Crest-cover metadata is not valid JSON: {metadata_path}"
        ) from error

    schema_version = raw.get("schema_version") if isinstance(raw, dict) else None
    if schema_version not in {1, 2}:
        raise CrestCoverError("Crest-cover metadata has an unsupported schema version")
    clubs = raw.get("clubs")
    if not isinstance(clubs, list):
        raise CrestCoverError("Crest-cover metadata must contain a clubs list")

    by_provider_id: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(clubs):
        label = f"clubs[{index}]"
        if not isinstance(item, dict):
            raise CrestCoverError(f"{label} must be an object")
        provider_id = item.get("provider_team_id")
        if (
            isinstance(provider_id, bool)
            or not isinstance(provider_id, int)
            or provider_id <= 0
        ):
            raise CrestCoverError(
                f"{label}.provider_team_id must be a positive integer"
            )
        if provider_id in by_provider_id:
            raise CrestCoverError(f"provider_team_id {provider_id} is duplicated")

        review_status = item.get("review_status")
        if review_status not in _REVIEW_STATUSES:
            raise CrestCoverError(
                f"{label}.review_status must be covered, not_required, or manual_review"
            )
        regions = validate_cover_regions(
            item.get("cover_regions"), label=f"{label}.cover_regions"
        )
        if review_status == "covered" and not regions:
            raise CrestCoverError(f"{label} is covered but has no cover regions")
        if review_status != "covered" and regions:
            raise CrestCoverError(
                f"{label} has cover regions but review_status is {review_status!r}"
            )

        normalized: dict[str, Any] = {
            "provider_team_id": provider_id,
            "review_status": review_status,
            "cover_regions": regions,
        }
        if schema_version == 2:
            confidence = item.get("coverage_confidence")
            if confidence not in _COVERAGE_CONFIDENCES:
                raise CrestCoverError(
                    f"{label}.coverage_confidence must be high, medium, low, "
                    "or unreviewed"
                )
            reviewed_at = item.get("reviewed_at")
            reviewed_digest = item.get("reviewed_crest_sha256")
            if review_status == "manual_review":
                if confidence != "unreviewed":
                    raise CrestCoverError(
                        f"{label} awaiting manual review must have "
                        "unreviewed confidence"
                    )
                if reviewed_at is not None or reviewed_digest is not None:
                    raise CrestCoverError(
                        f"{label} awaiting manual review cannot have review provenance"
                    )
            else:
                if confidence == "unreviewed":
                    raise CrestCoverError(
                        f"{label} with reviewed coverage cannot have "
                        "unreviewed confidence"
                    )
                valid_reviewed_at = isinstance(
                    reviewed_at, str
                ) and _REVIEWED_AT_PATTERN.fullmatch(reviewed_at)
                if not valid_reviewed_at:
                    raise CrestCoverError(
                        f"{label}.reviewed_at must be an ISO date (YYYY-MM-DD)"
                    )
                if (
                    not isinstance(reviewed_digest, str)
                    or not _SHA256_PATTERN.fullmatch(reviewed_digest)
                ):
                    raise CrestCoverError(
                        f"{label}.reviewed_crest_sha256 must be a lowercase "
                        "SHA-256 digest"
                    )
            normalized["coverage_confidence"] = confidence
            normalized["reviewed_at"] = reviewed_at
            normalized["reviewed_crest_sha256"] = reviewed_digest
        if "theme_colors" in item:
            normalized["theme_colors"] = validate_theme_colors(
                item["theme_colors"], label=f"{label}.theme_colors"
            )
        by_provider_id[provider_id] = normalized
    return by_provider_id


def validate_theme_colors(value: Any, *, label: str = "theme_colors") -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise CrestCoverError(f"{label} must be an object")
    if set(value) != {"primary", "secondary"}:
        raise CrestCoverError(f"{label} must contain exactly primary and secondary")
    colors: dict[str, str] = {}
    for key in ("primary", "secondary"):
        color = value[key]
        if not isinstance(color, str) or not _COLOR_PATTERN.fullmatch(color):
            raise CrestCoverError(f"{label}.{key} must be a #RRGGBB colour")
        colors[key] = color.upper()
    if (
        _color_distance(
            _hex_to_rgb(colors["primary"]), _hex_to_rgb(colors["secondary"])
        )
        < 40
    ):
        raise CrestCoverError(f"{label} colours are too similar")
    return colors


def validate_cover_regions(
    value: Any, *, label: str = "cover_regions"
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CrestCoverError(f"{label} must be a list")
    regions: list[dict[str, Any]] = []
    for index, raw_region in enumerate(value):
        region_label = f"{label}[{index}]"
        if not isinstance(raw_region, Mapping):
            raise CrestCoverError(f"{region_label} must be an object")
        shape = raw_region.get("shape", _RECTANGLE_SHAPE)
        if shape == _RECTANGLE_SHAPE:
            expected = {"x", "y", "width", "height", "shape"}
            if set(raw_region) - expected:
                raise CrestCoverError(f"{region_label} contains unsupported fields")
            x = _coordinate(raw_region.get("x"), f"{region_label}.x")
            y = _coordinate(raw_region.get("y"), f"{region_label}.y")
            width = _positive_coordinate(
                raw_region.get("width"), f"{region_label}.width"
            )
            height = _positive_coordinate(
                raw_region.get("height"), f"{region_label}.height"
            )
            if x + width > 1 + 1e-9 or y + height > 1 + 1e-9:
                raise CrestCoverError(
                    f"{region_label} extends outside the crest canvas"
                )
            regions.append(
                {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "shape": _RECTANGLE_SHAPE,
                }
            )
        elif shape == _POLYGON_SHAPE:
            if set(raw_region) != {"shape", "points"}:
                raise CrestCoverError(
                    f"{region_label} polygon must contain exactly shape and points"
                )
            raw_points = raw_region.get("points")
            if not isinstance(raw_points, list) or len(raw_points) < 3:
                raise CrestCoverError(
                    f"{region_label}.points must contain at least 3 points"
                )
            points: list[dict[str, float]] = []
            for point_index, raw_point in enumerate(raw_points):
                point_label = f"{region_label}.points[{point_index}]"
                if not isinstance(raw_point, Mapping) or set(raw_point) != {"x", "y"}:
                    raise CrestCoverError(f"{point_label} must contain exactly x and y")
                points.append(
                    {
                        "x": _coordinate(raw_point["x"], f"{point_label}.x"),
                        "y": _coordinate(raw_point["y"], f"{point_label}.y"),
                    }
                )
            if _polygon_area(points) <= 1e-5:
                raise CrestCoverError(f"{region_label} polygon has no usable area")
            regions.append({"shape": _POLYGON_SHAPE, "points": points})
        else:
            raise CrestCoverError(f"{region_label}.shape is unsupported: {shape!r}")
    return regions


def extract_theme_colors(
    image: Image.Image,
    override: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Extract deterministic, distinct theme colours from visible crest pixels."""
    if override is not None:
        return validate_theme_colors(override)

    rgba = image.convert("RGBA")
    histogram: Counter[tuple[int, int, int]] = Counter()
    for red, green, blue, alpha in rgba.getdata():
        if alpha < 16:
            continue
        # Stable five-bit bins reduce antialiasing noise without a random quantizer.
        bucket = (
            min(255, (red // 8) * 8 + 4),
            min(255, (green // 8) * 8 + 4),
            min(255, (blue // 8) * 8 + 4),
        )
        histogram[bucket] += alpha
    if not histogram:
        raise CrestCoverError(
            "A crest has no visible pixels for theme-colour extraction"
        )

    candidates = sorted(histogram.items(), key=lambda item: (-item[1], item[0]))[:64]
    total = sum(histogram.values())
    chromatic = [
        item
        for item in candidates
        if _chroma(item[0]) >= 36 and item[1] >= total * 0.01
    ]
    dominant_color, dominant_count = candidates[0]
    dominant_is_neutral = _chroma(dominant_color) < 28
    if dominant_is_neutral and dominant_count >= total * 0.45 and chromatic:
        primary = max(
            chromatic,
            key=lambda item: (item[1] * (1 + _chroma(item[0]) / 255), item[0]),
        )[0]
    else:
        primary = max(
            candidates,
            key=lambda item: (
                item[1] * (1 + min(_chroma(item[0]), 128) / 384),
                item[0],
            ),
        )[0]

    distinct = [
        item
        for item in candidates
        if _color_distance(primary, item[0]) >= 55
        and _contrast_ratio(primary, item[0]) >= 1.35
    ]
    if distinct:
        secondary = max(
            distinct,
            key=lambda item: (
                item[1] * (1 + _color_distance(primary, item[0]) / 255),
                item[0],
            ),
        )[0]
    else:
        black = (20, 20, 20)
        white = (244, 244, 244)
        secondary = max(
            (black, white), key=lambda color: _contrast_ratio(primary, color)
        )

    return {"primary": _rgb_to_hex(primary), "secondary": _rgb_to_hex(secondary)}


def generate_covered_crest(
    original: Image.Image,
    regions: Sequence[Mapping[str, Any]],
    theme_colors: Mapping[str, Any],
) -> Image.Image:
    """Return a covered RGBA copy while leaving the source image untouched."""
    normalized_regions = validate_cover_regions(list(regions))
    colors = validate_theme_colors(theme_colors)
    result = original.convert("RGBA").copy()
    if not normalized_regions:
        return result

    primary = (*_hex_to_rgb(colors["primary"]), 255)
    secondary_rgb = _hex_to_rgb(colors["secondary"])
    secondary = (*secondary_rgb, 255)
    width, height = result.size

    for region in normalized_regions:
        mask = Image.new("L", result.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        _draw_region(mask_draw, region, width, height, fill=255)

        cover = Image.new("RGBA", result.size, primary)
        pattern = Image.new("RGBA", result.size, (0, 0, 0, 0))
        pattern_draw = ImageDraw.Draw(pattern)
        bounds = _region_bounds(region, width, height)
        stripe_step = max(8, round(min(width, height) * 0.055))
        stripe_width = max(2, stripe_step // 4)
        start = bounds[0] - (bounds[3] - bounds[1]) - stripe_step
        end = bounds[2] + (bounds[3] - bounds[1]) + stripe_step
        for position in range(start, end, stripe_step):
            pattern_draw.line(
                [(position, bounds[3]), (position + bounds[3] - bounds[1], bounds[1])],
                fill=(*secondary_rgb, 72),
                width=stripe_width,
            )
        cover.alpha_composite(pattern)
        result.paste(cover, (0, 0), mask)

        border_width = max(2, round(min(width, height) * 0.012))
        result_draw = ImageDraw.Draw(result)
        _draw_region(
            result_draw,
            region,
            width,
            height,
            fill=None,
            outline=secondary,
            line_width=border_width,
        )

        # A small centre-circle detail gives the patch a football-pitch character.
        centre_x = (bounds[0] + bounds[2]) // 2
        centre_y = (bounds[1] + bounds[3]) // 2
        radius = max(2, min(bounds[2] - bounds[0], bounds[3] - bounds[1]) // 7)
        detail = Image.new("RGBA", result.size, (0, 0, 0, 0))
        detail_draw = ImageDraw.Draw(detail)
        detail_draw.ellipse(
            (
                centre_x - radius,
                centre_y - radius,
                centre_x + radius,
                centre_y + radius,
            ),
            outline=(*secondary_rgb, 150),
            width=max(1, border_width // 2),
        )
        result.alpha_composite(
            Image.composite(detail, Image.new("RGBA", result.size), mask)
        )

    return result


def save_covered_crest(
    original_path: Path | str,
    destination: Path | str,
    regions: Sequence[Mapping[str, Any]],
    theme_colors: Mapping[str, Any],
) -> None:
    source = Path(original_path)
    target = Path(destination)
    with Image.open(source) as opened:
        opened.load()
        covered = generate_covered_crest(opened, regions, theme_colors)
    target.parent.mkdir(parents=True, exist_ok=True)
    covered.save(target, format="PNG", optimize=True)


def region_area(region: Mapping[str, Any]) -> float:
    shape = region.get("shape", _RECTANGLE_SHAPE)
    if shape == _RECTANGLE_SHAPE:
        return float(region["width"]) * float(region["height"])
    if shape == _POLYGON_SHAPE:
        return _polygon_area(region["points"])
    raise CrestCoverError(f"Unsupported cover shape: {shape!r}")


def _draw_region(
    draw: ImageDraw.ImageDraw,
    region: Mapping[str, Any],
    width: int,
    height: int,
    *,
    fill: Any,
    outline: Any = None,
    line_width: int = 1,
) -> None:
    if region["shape"] == _RECTANGLE_SHAPE:
        bounds = _region_bounds(region, width, height)
        radius = max(2, min(bounds[2] - bounds[0], bounds[3] - bounds[1]) // 4)
        draw.rounded_rectangle(
            bounds,
            radius=radius,
            fill=fill,
            outline=outline,
            width=line_width,
        )
        return
    points = [
        (
            round(float(point["x"]) * (width - 1)),
            round(float(point["y"]) * (height - 1)),
        )
        for point in region["points"]
    ]
    draw.polygon(points, fill=fill)
    if outline is not None:
        draw.line([*points, points[0]], fill=outline, width=line_width, joint="curve")


def _region_bounds(
    region: Mapping[str, Any], width: int, height: int
) -> tuple[int, int, int, int]:
    if region["shape"] == _RECTANGLE_SHAPE:
        x0 = math.floor(float(region["x"]) * width)
        y0 = math.floor(float(region["y"]) * height)
        x1 = min(
            width - 1,
            math.ceil((float(region["x"]) + float(region["width"])) * width) - 1,
        )
        y1 = min(
            height - 1,
            math.ceil((float(region["y"]) + float(region["height"])) * height) - 1,
        )
        return x0, y0, x1, y1
    xs = [round(float(point["x"]) * (width - 1)) for point in region["points"]]
    ys = [round(float(point["y"]) * (height - 1)) for point in region["points"]]
    return min(xs), min(ys), max(xs), max(ys)


def _coordinate(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        raise CrestCoverError(f"{label} must be a finite number")
    coordinate = float(value)
    if coordinate < 0 or coordinate > 1:
        raise CrestCoverError(f"{label} must be between 0 and 1")
    return coordinate


def _positive_coordinate(value: Any, label: str) -> float:
    coordinate = _coordinate(value, label)
    if coordinate <= 0:
        raise CrestCoverError(f"{label} must be greater than 0")
    return coordinate


def _polygon_area(points: Sequence[Mapping[str, Any]]) -> float:
    return (
        abs(
            sum(
                float(point["x"]) * float(points[(index + 1) % len(points)]["y"])
                - float(points[(index + 1) % len(points)]["x"]) * float(point["y"])
                for index, point in enumerate(points)
            )
        )
        / 2
    )


def _chroma(color: tuple[int, int, int]) -> int:
    return max(color) - min(color)


def _color_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    # Red-mean distance is deterministic and more perceptual than RGB Euclidean.
    red_mean = (first[0] + second[0]) / 2
    red = first[0] - second[0]
    green = first[1] - second[1]
    blue = first[2] - second[2]
    return math.sqrt(
        (2 + red_mean / 256) * red * red
        + 4 * green * green
        + (2 + (255 - red_mean) / 256) * blue * blue
    )


def _relative_luminance(color: tuple[int, int, int]) -> float:
    channels = []
    for value in color:
        channel = value / 255
        channels.append(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
    )


def _rgb_to_hex(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02X}" for channel in color)
