# Crest Quest

Crest Quest is a server-authoritative football crest guessing game built with React, Vite, FastAPI, SQLite, and packaged football data. Players choose one of eight leagues—or All Leagues—and a 30, 60, or 90-second round.

The original implementation plan is in [`writing-block.md`](writing-block.md).

## Features

- Eight supported leagues plus All Leagues
- 30, 60, and 90-second server-controlled rounds
- Four opaque answer choices with wrong-answer removal
- 3/2/1/0 attempt scoring, first-attempt streaks, repeated clean-three bonuses, and flawless 2× rounds
- No target repetition until the selected target pool is exhausted
- Opaque question and answer tokens; correct answers and provider IDs remain server-side
- Covered and original crest assets served statefully through `/api/questions/{opaque_token}/crest`
- 27 independent persistent top-10 leaderboards
- Retryable failed leaderboard submissions
- Responsive phone, tablet, and desktop UI
- Keyboard answers, screen-reader announcements, reduced-motion support, and optional sound/haptics
- Resumable football-data.org importer with normalized transparent 256×256 original and pre-generated covered PNG assets

## Requirements

- Python 3.12+
- Node.js 22+
- npm 10+
- Cairo runtime for SVG crest conversion
- A football-data.org v4 API key for seasonal data imports

On macOS, Cairo can be installed with Homebrew if it is not already available:

```bash
brew install cairo
```

## Install

```bash
cd /Users/paulmcevoy/crestquest
make install
```

This creates `.venv`, installs development and importer dependencies, and installs the frontend packages.

## Import current football data

The game never contacts football-data.org during gameplay. Current teams and crests must be imported before starting the game.

Export the API key in the current shell without adding it to source control:

```bash
export FOOTBALL_DATA_API_KEY='your-football-data-api-key'
make import-data
make validate-data
```

The importer:

1. Retrieves the football-data.org competition catalogue.
2. Verifies all eight competition codes against the accessible live catalogue.
3. Retrieves each competition's current season and current teams.
4. Downloads or reuses cached source crests.
5. Centers each crest on a transparent 256×256 RGBA PNG without stretching it.
6. Applies the reviewed normalized regions in `data/crest_cover_metadata.json`, extracts deterministic theme colours (or uses manual overrides), and generates a separate covered PNG.
7. Validates a staging dataset before atomically publishing `data/clubs.json`, `data/crests/`, and `data/covered-crests/`.
8. Reports every missing annotation or invalid team/crest and exits unsuccessfully instead of publishing partial data.

`make validate-data` also prints the crest-cover review report, including active, not-required, manual-review, missing, invalid, and suspiciously large regions.

Use a full crest refresh when providers retain a URL but change its contents:

```bash
FOOTBALL_DATA_API_KEY='your-key' \
  .venv/bin/python scripts/import_football_data.py --refresh-crests
```

Importer source-image cache files are stored under `var/football-data-cache/` and are not committed.

## Run locally

```bash
make dev
```

Open:

- Frontend: <http://localhost:5173>
- Crest Quest API: <http://localhost:8010>
- Development API docs: <http://localhost:8010/api/docs>

Development uses port `8010` for FastAPI to avoid conflicting with Poké-Guesser on port `8000`. Vite proxies `/api` to that service. Press `Ctrl+C` to stop both processes.

If the UI reports that club data is not ready, run the import and validation commands above, then use **Retry service setup** or restart the development process.

## Tests and validation

```bash
make test
make lint
make build
make validate-data
make release-check
```

Focused commands:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check server src scripts tests
npm test --prefix frontend
npm run test:e2e:webkit --prefix frontend
npm run build --prefix frontend
```

The suite covers scoring, streaks, bonuses, expiry, target/distractor selection, importer retries and resumability, asset validation, API answer-leak prevention, token reuse, all 27 leaderboard partitions, ranking tie-breakers, SQLite persistence, failed writes, keyboard behavior, accessibility, and responsive viewport smoke tests.

The Playwright suite uses WebKit with touch input and checks the complete active-round layout at the supported iPad and iPhone viewport matrix. Install its browser runtime once with `npm exec --prefix frontend playwright install webkit`. Browser-test screenshots are written to `var/screenshots/responsive/`.

## Touch-first responsive layout

Active gameplay is a bounded `100dvh` workspace so browser chrome changes do not create document scrolling. The workspace applies all four `env(safe-area-inset-*)` values, hides nonessential account/footer controls, and keeps the React round component mounted during viewport and orientation changes. Setup, result, service, and leaderboard views remain normally scrollable.

The main layout rules in `frontend/src/styles.css` are based on available geometry rather than device detection:

- Landscape/default: crest and reveal on the left; statistics, feedback, and a 2×2 answer grid on the right.
- Portrait (`max-aspect-ratio: 1/1`): compact crest above statistics and controls; phones use four stacked answers, while portrait layouts at least 700px wide use a 2×2 grid.
- Narrow layouts (`max-width: 680px`): reduce nonessential spacing and labels while retaining 44px minimum controls.
- Short landscape (`max-height: 500px` and `min-aspect-ratio: 4/3`): condensed top information, two columns, and a 2×2 answer grid sized to keep the full round visible.
- Very narrow non-game views (`max-width: 450px`): stack setup/result actions and leaderboard filters for comfortable scrolling.

Sizes use Grid/Flexbox, `clamp()`, aspect-ratio media queries, and dynamic viewport units. Button pressed states do not depend on hover, browser zoom remains enabled, and reduced-motion preferences disable nonessential transitions.

## Seasonal refresh process

Run this after promotions, relegations, provider crest changes, or before a deployment:

```bash
export FOOTBALL_DATA_API_KEY='your-football-data-api-key'
.venv/bin/python scripts/import_football_data.py --refresh-crests
.venv/bin/python scripts/validate_data.py
.venv/bin/python -m pytest
npm test --prefix frontend
```

Review the importer summary, crest-cover report, and version-control diff. Commit `data/crest_cover_metadata.json`, generated `data/clubs.json`, `data/crests/*.png`, and `data/covered-crests/*.png`; never commit `.env`, cache files, or the API key. Deploy only after validation succeeds.

## Crest cover annotations and colour overrides

`data/crest_cover_metadata.json` is the maintained source for manual review. Its entries are keyed by stable provider team ID, so unchanged reviewed clubs remain approved when new leagues are imported. Every provider team ID must occur exactly once with one of these statuses:

- `covered`: one or more reviewed normalized regions are required.
- `not_required`: the crest has no answer-revealing text and `cover_regions` must be empty.
- `manual_review`: no region is approved yet; the validation report lists the club explicitly.

Schema v2 also records `coverage_confidence`, `reviewed_at`, and `reviewed_crest_sha256`. The digest is calculated from normalized RGBA pixels rather than PNG encoding. Preparation fails if a provider reuses an ID or URL for changed crest artwork, preventing stale approval from being carried forward. Reviewed entries use `high`, `medium`, or `low` confidence; `manual_review` entries use `unreviewed` with null provenance.

Rectangle coordinates are relative to the complete 256×256 crest canvas and remain valid at every rendered size:

```json
{
  "provider_team_id": 123,
  "review_status": "covered",
  "coverage_confidence": "high",
  "reviewed_at": "2026-08-27",
  "reviewed_crest_sha256": "<64-character lowercase SHA-256 digest>",
  "theme_colors": {
    "primary": "#C8102E",
    "secondary": "#FFFFFF"
  },
  "cover_regions": [
    {
      "x": 0.18,
      "y": 0.68,
      "width": 0.64,
      "height": 0.14,
      "shape": "rounded_rectangle"
    }
  ]
}
```

`shape` defaults to `rounded_rectangle`. For curved or diagonal text, use a polygon with at least three normalized points. Omit `theme_colors` to use deterministic extraction; include both colours to override it. Colours must be distinct `#RRGGBB` values.

After changing annotations or overrides, run:

```bash
make prepare-crests
make validate-data
```

The cover report groups all crests by confidence and separately lists missing annotations, `manual_review` entries, and artwork changed since review. When adding leagues, retain existing metadata entries and add only the new provider IDs as `manual_review`; unchanged high-confidence entries require no new obscuring work. If an existing crest changes, update its regions as needed and replace its review date and pixel digest only after visual approval.

Visually compare untouched files in `data/crests/` with generated files in `data/covered-crests/`. Never replace an original with its covered derivative.

For a full local visual review, run:

```bash
make review-crests
```

This generates and opens the ignored page `var/crest-review/index.html`. It shows every original and covered crest side by side, measures the percentage of visible original pixels changed by cover rendering, highlights crests over 50%, persists selections in browser local storage, and exports selected provider IDs as text or JSON.

## API

```text
GET  /api/state
PUT  /api/player
POST /api/player/logout

POST /api/round/start
POST /api/round/guess
POST /api/round/advance
POST /api/round/expire

GET  /api/questions/{opaque_token}/crest

GET  /api/leaderboard?scope={scope}&duration={duration}
POST /api/leaderboard/retry

POST /api/setup/retry
GET  /api/live
GET  /api/health
```

Only opaque question/answer tokens and answer display names are sent before an answer. Provider IDs, source URLs, packaged crest paths, original asset URLs, and marked correct answers are not exposed. The opaque crest endpoint serves the pre-generated covered asset while unanswered and switches to the untouched original only after a correct answer or expiry.

## Production container

```bash
docker build -t crest-quest .
docker run --rm \
  -p 8000:8000 \
  -e CREST_QUEST_SESSION_SECRET='replace-with-a-long-random-secret' \
  -v crest-quest-data:/data \
  crest-quest
```

Open <http://localhost:8000>. The container serves the built frontend and API from one origin and intentionally uses one Uvicorn worker because active rounds are held in memory. The Docker build runs packaged-data validation and fails if any league, club, or crest asset is missing or invalid.

## Railway deployment

1. Import and validate the packaged dataset before building.
2. Deploy using `railway.json` and the repository `Dockerfile`.
3. Set `CREST_QUEST_SESSION_SECRET` to a long random secret.
4. Attach a persistent Railway volume at `/data`.
5. Keep one replica while round state remains in memory.
6. Verify `/api/health`, all league scopes, all durations, and leaderboard persistence after a restart.

The production container writes SQLite to `/data/leaderboard.sqlite3` and reads gameplay data only from `/app/data`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `FOOTBALL_DATA_API_KEY` | Import-only football-data.org credential; never used by browser gameplay |
| `CREST_QUEST_ENV` | `development`, `test`, or `production` |
| `CREST_QUEST_SESSION_SECRET` | Signs HTTP-only browser sessions; required and at least 32 characters in production |
| `CREST_QUEST_FRONTEND_DIST` | Optional built frontend path override |
| `CREST_QUEST_DATA_PATH` | Optional packaged manifest path override |
| `CREST_QUEST_LEADERBOARD_PATH` | SQLite path; `/data/leaderboard.sqlite3` in Docker |

Never expose the football-data.org key through a `VITE_` variable, API response, URL, client bundle, or logs.

## Project layout

```text
data/          Packaged manifest and normalized crest assets
frontend/      React/Vite client and component tests
scripts/       Import, validation, and development commands
server/        FastAPI transport, sessions, rounds, and SQLite leaderboard
src/           Framework-independent club, scoring, selection, and game domains
tests/         Python unit, integration, persistence, and security tests
```
