# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "clubs.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "var" / "crest-review" / "index.html"


def visible_obscured_fraction(original_path: Path, covered_path: Path) -> float:
    """Return the fraction of visible original pixels changed by cover rendering."""
    with Image.open(original_path) as original_image:
        original = original_image.convert("RGBA")
    with Image.open(covered_path) as covered_image:
        covered = covered_image.convert("RGBA")
    if original.size != covered.size:
        raise ValueError(
            f"Crest dimensions differ: {original_path} is {original.size}, "
            f"{covered_path} is {covered.size}"
        )

    visible = 0
    obscured = 0
    for original_pixel, covered_pixel in zip(
        original.getdata(), covered.getdata(), strict=True
    ):
        if original_pixel[3] < 16:
            continue
        visible += 1
        if original_pixel != covered_pixel:
            obscured += 1
    return obscured / visible if visible else 0.0


def build_review_records(
    manifest_path: Path | str = DEFAULT_MANIFEST,
    output_path: Path | str = DEFAULT_OUTPUT,
) -> list[dict[str, Any]]:
    manifest = Path(manifest_path)
    output = Path(output_path)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    clubs = raw.get("clubs") if isinstance(raw, dict) else None
    if not isinstance(clubs, list):
        raise ValueError("Club manifest must contain a clubs list")

    data_root = manifest.parent.resolve()
    records: list[dict[str, Any]] = []
    for index, club in enumerate(clubs):
        if not isinstance(club, dict):
            raise ValueError(f"clubs[{index}] must be an object")
        original_asset = club.get("crest")
        covered_asset = club.get("covered_crest")
        if not isinstance(original_asset, str) or not isinstance(covered_asset, str):
            raise ValueError(f"clubs[{index}] is missing crest assets")
        original_path = data_root / original_asset
        covered_path = data_root / covered_asset
        fraction = visible_obscured_fraction(original_path, covered_path)
        records.append(
            {
                "provider_team_id": club.get("provider_id"),
                "name": club.get("name"),
                "league": club.get("league"),
                "coverage_percent": round(fraction * 100, 1),
                "original_crest": original_asset,
                "covered_crest": covered_asset,
                "original_src": os.path.relpath(
                    original_path, output.parent
                ).replace(os.sep, "/"),
                "covered_src": os.path.relpath(
                    covered_path, output.parent
                ).replace(os.sep, "/"),
            }
        )
    return sorted(records, key=lambda record: str(record["name"]).casefold())


def render_review_page(
    records: list[dict[str, Any]], *, generated_at: str
) -> str:
    encoded_records = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crest Quest cover review</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b1020;
      --panel: #151c31;
      --panel-strong: #1d2742;
      --text: #f3f6ff;
      --muted: #aab4cc;
      --accent: #71a7ff;
      --warning: #ffb84d;
      --danger: #ff6b76;
      --success: #55d69e;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); }}
    button, input, select {{ font: inherit; }}
    .toolbar {{
      position: sticky; top: 0; z-index: 10; padding: 16px clamp(14px, 3vw, 36px);
      background: color-mix(in srgb, var(--bg) 92%, transparent);
      border-bottom: 1px solid #2c3859; backdrop-filter: blur(14px);
    }}
    .toolbar h1 {{ margin: 0 0 5px; font-size: clamp(1.25rem, 3vw, 1.8rem); }}
    .toolbar p {{ margin: 0; color: var(--muted); font-size: .9rem; }}
    .controls {{ display: flex; flex-wrap: wrap; gap: 9px; margin-top: 14px; }}
    .controls input, .controls select, button {{
      min-height: 42px; border: 1px solid #394868; border-radius: 9px;
      background: var(--panel-strong); color: var(--text); padding: 8px 11px;
    }}
    .controls input {{ min-width: min(280px, 100%); flex: 1; }}
    button {{ cursor: pointer; }}
    button:hover {{ border-color: var(--accent); }}
    button.primary {{ background: #2456a4; border-color: #4e88e8; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 14px; margin-top: 12px; color: var(--muted); }}
    .summary strong {{ color: var(--text); }}
    main {{ padding: 20px clamp(12px, 2.5vw, 34px) 50px; }}
    .grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
      gap: 14px;
    }}
    .card {{
      position: relative; display: block; border: 2px solid #283553; border-radius: 14px;
      background: var(--panel); overflow: hidden; cursor: pointer;
      transition: border-color .15s, transform .15s, background .15s;
    }}
    .card:hover {{ transform: translateY(-2px); border-color: #53688f; }}
    .card.selected {{ border-color: var(--warning); background: #262238; }}
    .card.over-limit:not(.selected) {{ border-color: var(--danger); }}
    .card input[type="checkbox"] {{
      position: absolute; top: 12px; right: 12px; width: 24px; height: 24px;
      accent-color: var(--warning); z-index: 2;
    }}
    .card-header {{ padding: 12px 48px 9px 13px; min-height: 70px; }}
    .club-name {{ font-size: 1.04rem; font-weight: 750; line-height: 1.25; }}
    .club-meta {{ color: var(--muted); font-size: .8rem; margin-top: 4px; }}
    .images {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: #33405d; }}
    figure {{ margin: 0; background: #090d18; padding: 8px 8px 6px; text-align: center; }}
    figure img {{ width: 100%; height: 150px; object-fit: contain; display: block; }}
    figcaption {{ margin-top: 5px; color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; }}
    .coverage {{ padding: 11px 13px 13px; }}
    .coverage-line {{ display: flex; justify-content: space-between; gap: 10px; font-size: .88rem; }}
    .percentage {{ font-weight: 800; color: var(--success); }}
    .over-limit .percentage {{ color: var(--danger); }}
    .meter {{ margin-top: 7px; height: 8px; background: #080c17; border-radius: 999px; overflow: hidden; }}
    .meter > span {{ display: block; height: 100%; background: var(--success); }}
    .over-limit .meter > span {{ background: var(--danger); }}
    .empty {{ color: var(--muted); text-align: center; padding: 80px 20px; }}
    .toast {{
      position: fixed; right: 20px; bottom: 20px; background: #263555; border: 1px solid #5976aa;
      border-radius: 10px; padding: 11px 15px; opacity: 0; transform: translateY(12px);
      pointer-events: none; transition: .2s; z-index: 20;
    }}
    .toast.show {{ opacity: 1; transform: translateY(0); }}
    @media (max-width: 560px) {{
      .controls > * {{ width: 100%; }}
      .grid {{ grid-template-columns: 1fr; }}
      figure img {{ height: 130px; }}
    }}
  </style>
</head>
<body>
  <header class="toolbar">
    <h1>Crest cover review</h1>
    <p>Select every covered crest that hides too much. Coverage measures changed pixels only within the visible original crest.</p>
    <div class="controls">
      <input id="search" type="search" placeholder="Search club or league">
      <select id="filter" aria-label="Filter crests">
        <option value="all">All crests</option>
        <option value="over-limit">Over 50%</option>
        <option value="selected">Selected</option>
        <option value="unselected">Not selected</option>
      </select>
      <select id="sort" aria-label="Sort crests">
        <option value="coverage-desc">Most obscured first</option>
        <option value="name">Club name</option>
        <option value="league">League</option>
      </select>
      <button id="select-over">Select all over 50%</button>
      <button id="clear">Clear selection</button>
      <button id="copy" class="primary">Copy selected list</button>
      <button id="download" class="primary">Download JSON</button>
    </div>
    <div class="summary">
      <span>Showing <strong id="showing">0</strong> of <strong>{len(records)}</strong></span>
      <span>Selected <strong id="selected-count">0</strong></span>
      <span>Over 50% <strong id="over-count">0</strong></span>
      <span>Generated <strong>{generated_at}</strong></span>
    </div>
  </header>
  <main>
    <div id="grid" class="grid"></div>
    <div id="empty" class="empty" hidden>No crests match this view.</div>
  </main>
  <div id="toast" class="toast" role="status"></div>
  <script>
    const clubs = {encoded_records};
    const storageKey = "crestquest.cover-review.selection.v1";
    const selected = new Set(
      JSON.parse(localStorage.getItem(storageKey) || "[]").map(Number)
    );
    const grid = document.querySelector("#grid");
    const empty = document.querySelector("#empty");
    const search = document.querySelector("#search");
    const filter = document.querySelector("#filter");
    const sort = document.querySelector("#sort");

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, character => ({{
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }})[character]);
    }}

    function persist() {{
      localStorage.setItem(storageKey, JSON.stringify([...selected].sort((a, b) => a - b)));
    }}

    function selectedClubs() {{
      return clubs.filter(club => selected.has(Number(club.provider_team_id)));
    }}

    function exportPayload() {{
      return {{
        schema_version: 1,
        generated_at: {json.dumps(generated_at)},
        metric: "percentage of non-transparent original crest pixels changed by the covered asset",
        threshold_percent: 50,
        selected_count: selected.size,
        clubs: selectedClubs().map(club => ({{
          provider_team_id: club.provider_team_id,
          name: club.name,
          league: club.league,
          coverage_percent: club.coverage_percent,
          original_crest: club.original_crest,
          covered_crest: club.covered_crest
        }}))
      }};
    }}

    function showToast(message) {{
      const toast = document.querySelector("#toast");
      toast.textContent = message;
      toast.classList.add("show");
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(() => toast.classList.remove("show"), 1800);
    }}

    function visibleClubs() {{
      const query = search.value.trim().toLocaleLowerCase();
      const mode = filter.value;
      const visible = clubs.filter(club => {{
        const id = Number(club.provider_team_id);
        const matchesSearch = !query || `${{club.name}} ${{club.league}}`.toLocaleLowerCase().includes(query);
        const matchesFilter = mode === "all"
          || (mode === "over-limit" && club.coverage_percent > 50)
          || (mode === "selected" && selected.has(id))
          || (mode === "unselected" && !selected.has(id));
        return matchesSearch && matchesFilter;
      }});
      if (sort.value === "name") {{
        visible.sort((a, b) => a.name.localeCompare(b.name));
      }} else if (sort.value === "league") {{
        visible.sort((a, b) => a.league.localeCompare(b.league) || a.name.localeCompare(b.name));
      }} else {{
        visible.sort((a, b) => b.coverage_percent - a.coverage_percent || a.name.localeCompare(b.name));
      }}
      return visible;
    }}

    function render() {{
      const visible = visibleClubs();
      grid.innerHTML = visible.map(club => {{
        const id = Number(club.provider_team_id);
        const checked = selected.has(id);
        const overLimit = club.coverage_percent > 50;
        return `<label class="card ${{checked ? "selected" : ""}} ${{overLimit ? "over-limit" : ""}}">
          <input type="checkbox" data-provider-id="${{id}}" ${{checked ? "checked" : ""}} aria-label="Mark ${{escapeHtml(club.name)}} as too obscured">
          <div class="card-header">
            <div class="club-name">${{escapeHtml(club.name)}}</div>
            <div class="club-meta">${{escapeHtml(club.league)}} · provider ${{id}}</div>
          </div>
          <div class="images">
            <figure><img src="${{escapeHtml(club.original_src)}}" alt="Original ${{escapeHtml(club.name)}} crest"><figcaption>Original</figcaption></figure>
            <figure><img src="${{escapeHtml(club.covered_src)}}" alt="Covered ${{escapeHtml(club.name)}} crest"><figcaption>Covered</figcaption></figure>
          </div>
          <div class="coverage">
            <div class="coverage-line"><span>Visible pixels obscured</span><span class="percentage">${{club.coverage_percent.toFixed(1)}}%</span></div>
            <div class="meter"><span style="width:${{Math.min(100, club.coverage_percent)}}%"></span></div>
          </div>
        </label>`;
      }}).join("");
      empty.hidden = visible.length !== 0;
      document.querySelector("#showing").textContent = visible.length;
      document.querySelector("#selected-count").textContent = selected.size;
      document.querySelector("#over-count").textContent = clubs.filter(club => club.coverage_percent > 50).length;
    }}

    grid.addEventListener("change", event => {{
      const checkbox = event.target.closest("input[data-provider-id]");
      if (!checkbox) return;
      const id = Number(checkbox.dataset.providerId);
      checkbox.checked ? selected.add(id) : selected.delete(id);
      persist();
      render();
    }});
    [search, filter, sort].forEach(control => control.addEventListener("input", render));
    document.querySelector("#select-over").addEventListener("click", () => {{
      clubs.filter(club => club.coverage_percent > 50)
        .forEach(club => selected.add(Number(club.provider_team_id)));
      persist(); render();
    }});
    document.querySelector("#clear").addEventListener("click", () => {{
      selected.clear(); persist(); render();
    }});
    document.querySelector("#copy").addEventListener("click", async () => {{
      const text = selectedClubs()
        .map(club => `${{club.provider_team_id}}\t${{club.name}}\t${{club.coverage_percent.toFixed(1)}}%`)
        .join(String.fromCharCode(10));
      if (!text) return showToast("No crests selected");
      try {{
        await navigator.clipboard.writeText(text);
      }} catch {{
        const area = document.createElement("textarea");
        area.value = text; document.body.append(area); area.select();
        document.execCommand("copy"); area.remove();
      }}
      showToast(`Copied ${{selected.size}} selected crest${{selected.size === 1 ? "" : "s"}}`);
    }});
    document.querySelector("#download").addEventListener("click", () => {{
      const payload = exportPayload();
      if (!payload.selected_count) return showToast("No crests selected");
      const blob = new Blob(
        [JSON.stringify(payload, null, 2) + String.fromCharCode(10)],
        {{type: "application/json"}}
      );
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "crest-cover-review-selection.json";
      link.click();
      URL.revokeObjectURL(link.href);
      showToast(`Downloaded ${{payload.selected_count}} selections`);
    }});
    render();
  </script>
</body>
</html>
"""


def generate_review_page(
    manifest_path: Path | str = DEFAULT_MANIFEST,
    output_path: Path | str = DEFAULT_OUTPUT,
) -> tuple[Path, list[dict[str, Any]]]:
    output = Path(output_path)
    records = build_review_records(manifest_path, output)
    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_review_page(records, generated_at=generated_at), encoding="utf-8"
    )
    return output, records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a local, selectable crest-cover review page."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--open", action="store_true", help="open the page in a browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output, records = generate_review_page(args.manifest, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Crest review page generation failed: {error}", file=sys.stderr)
        return 1
    over_limit = sum(record["coverage_percent"] > 50 for record in records)
    print(
        f"Generated {output} with {len(records)} crests; "
        f"{over_limit} exceed 50% visible-pixel coverage."
    )
    if args.open:
        webbrowser.open(output.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
