# Crest Quest — Implementation Plan

## Objective

Build a new football crest guessing game called **Crest Quest**. It should borrow the successful gameplay and technical ideas from Poké-Guesser but remain a completely separate project.

Use the existing Poké-Guesser repository as a reference only. Do not modify it.

## Version-one scope

Include current teams from:

- Premier League
- Bundesliga
- La Liga
- Primeira Liga
- Ligue 1
- Serie A
- Eredivisie


Also provide an **All Leagues** mode.

Leave the League of Ireland for a later release.

## Data source

Use the football-data.org v4 API.

Read the API key from:

```text
FOOTBALL_DATA_API_KEY
```

Never expose the key to the frontend, commit it to Git or include it in logs.

Create an import script that:

1. Retrieves the current season and current teams for each supported competition.
2. Downloads each team’s crest.
3. Records the team name, provider ID, league, season and source URL.
4. Converts crests to a consistent transparent 256×256 PNG without stretching them.
5. Saves everything as packaged application data.
6. Can be safely rerun after promotion, relegation or crest changes.
7. Produces a clear report for missing or invalid crests.

The deployed game must use packaged data and must not contact football-data.org during gameplay.

Expected competition codes should be verified against the API rather than assumed. Version-one codes are `PL`, `BL1`, `PD`, `PPL`, `FL1`, `SA` and `DED`.

## Gameplay

Before each round, the player chooses:

- All Leagues or one individual league
- 30, 60 or 90 seconds

Each question must:

1. Show one full-colour club crest.
2. Show four club names.
3. Contain exactly one correct answer.
4. Remove an incorrect selection so the player can try again.
5. Move immediately to the next crest after a correct answer.
6. Avoid repeating a club until the available target pool has been exhausted.

For an individual-league game, all four answers should come from that league.

For All Leagues mode, choose distractors randomly from all supported clubs while ensuring at least one option comes from another league.

There are no difficulty settings.

## Scoring

Match all existing Poké-Guesser scoring rules:

- Correct on attempt one: 3 points
- Correct on attempt two: 2 points
- Correct on attempt three: 1 point
- Correct on attempt four: 0 points
- Every three consecutive first-attempt correct answers: +5 points
- The clean-three counter resets after a wrong answer or after awarding the bonus
- The visible streak counts consecutive first-attempt correct answers
- A wrong answer resets the streak
- A round with at least three correct answers and no wrong selections receives a 2× multiplier at expiry
- The server, not the browser, controls scoring and round deadlines

## Screens

Create these main screens:

1. Username/login
2. Game setup
3. Active round
4. Result
5. Leaderboard
6. Setup or service error

The active round should show:

- Crest Quest title
- Countdown and progress bar
- League selection
- Question number
- Score
- Number of clubs named
- Streak
- Club crest
- Four answer buttons
- Wrong/correct feedback
- Sound and haptics toggle

Wrong answers should shake and collapse. After a correct answer, briefly show the club name, crest and points awarded while the next question loads.

The result screen should show:

- Final score
- Clubs named
- Best streak
- Clean-three bonuses
- Flawless multiplier, when earned
- Final unanswered club
- Play again
- Show leaderboard

Support phone, tablet and desktop layouts, keyboard operation, reduced-motion preferences and screen-reader status announcements.

## Prevent answer leakage

The frontend must not receive the correct team ID or name before the question is answered.

Do not expose a provider team ID in the crest URL because it could be matched to an answer ID.

Use an opaque question token, for example:

```text
/api/questions/{opaque_token}/crest
```

The backend session should privately map that token to the correct team. The play-state response should contain only:

- Opaque question token
- Opaque answer IDs
- Four answer names
- Removed answer IDs
- Points currently available
- Round statistics and deadline

Only reveal the correct club after a correct answer or when the round expires.

## Leaderboards

Create separate top-10 leaderboards for every combination of:

- Seven individual leagues plus All Leagues
- 30, 60 and 90 seconds

That produces **24 leaderboards** in version one. Adding the League of Ireland later will increase this to 27.

Keep the Poké-Guesser leaderboard behaviour:

- Save every positive qualifying round
- Allow the same player to appear multiple times
- Do not restrict players to one personal-best entry
- Automatically open the leaderboard when a submitted score reaches its top 10
- Allow retrying a failed score submission
- Keep anonymous browser identity and usernames

Rank entries using:

1. Higher score
2. More clubs named
3. Fewer incorrect selections
4. Higher best streak
5. Earlier submission

Store the selected league scope and duration with every score.

## Architecture

Use:

- React and Vite frontend
- FastAPI backend
- SQLite leaderboard
- Packaged JSON and crest assets
- Docker deployment
- Railway-compatible configuration

Suggested structure:

```text
crest-quest/
  data/
    clubs.json
    crests/
  frontend/
    src/
      components/
      screens/
      game/
      api/
  server/
    app.py
    game_service.py
    leaderboard.py
    models.py
  src/
    scoring.py
    distractors.py
    club_data.py
  scripts/
    import_football_data.py
    validate_data.py
    dev.py
  tests/
  Dockerfile
  railway.json
  README.md
```

Keep React components smaller than the Poké-Guesser implementation. Separate setup, round, result and leaderboard screens from controller logic.

Use one backend worker initially if round sessions remain in memory. Store leaderboard data on a Railway volume.

## API outline

Suggested endpoints:

```text
GET  /api/state
PUT  /api/player
POST /api/player/logout

POST /api/round/start
POST /api/round/guess
POST /api/round/advance
POST /api/round/expire

GET  /api/questions/{token}/crest

GET  /api/leaderboard?scope={scope}&duration={duration}
POST /api/leaderboard/retry

POST /api/setup/retry
```

Starting a round must validate that the requested scope and duration are supported.

## Testing

Add automated tests for:

- 3/2/1/0 scoring
- Wrong-answer removal
- Streak resets
- Repeated clean-three bonuses
- Flawless 2× multiplier
- 30/60/90-second expiry
- No target repetition before pool exhaustion
- League-only target and distractor selection
- All Leagues selection
- No correct-answer leakage in API responses or crest URLs
- Invalid or reused answer IDs
- Leaderboard separation by league and duration
- Ranking tie-breakers
- Multiple entries from one player
- SQLite persistence
- Failed leaderboard writes
- Missing or invalid crest data
- football-data.org import retries and resumability
- Responsive frontend smoke tests
- Keyboard and accessibility checks

## Delivery stages

### Stage 1: Foundation

- Create the new repository.
- Scaffold FastAPI and React.
- Add development and production commands.
- Add Docker and Railway configuration.

### Stage 2: Football data

- Implement the football-data.org importer.
- Import the seven leagues.
- Download and normalise crests.
- Validate the packaged dataset.

### Stage 3: Game engine

- Implement target pools, distractors, scoring, streaks, bonuses and expiry.
- Write domain-level tests first.
- Ensure correct answers remain private.

### Stage 4: API

- Add sessions and round endpoints.
- Serve crests through opaque question tokens.
- Add API flow and security tests.

### Stage 5: Frontend

- Build username, setup, round, result and error screens.
- Add animations, sounds, haptics and responsive layouts.
- Use the approved Crest Quest visual direction from the mock-up.

### Stage 6: Leaderboards

- Add SQLite storage.
- Partition rankings by scope and duration.
- Add result submission, retry and automatic top-10 display.

### Stage 7: Verification and deployment

- Run all automated tests.
- Test every league and duration.
- Test phone, tablet and desktop layouts.
- Verify that no API key or correct answer is exposed.
- Deploy to Railway with persistent leaderboard storage.
- Document the seasonal data-refresh process.

## Version-one acceptance criteria

The implementation is complete when:

- All eight supported leagues contain their current clubs and valid crests.
- Players can select All Leagues or one league.
- Players can select 30, 60 or 90 seconds.
- Every question displays one crest and four answers.
- All scoring and bonus rules work correctly.
- Correct answers cannot be discovered from the pre-answer API response.
- All 27 leaderboards work independently.
- Leaderboard data survives a deployment restart.
- The game works well on phone, tablet and desktop.
- The football-data.org API key remains server-side and secret.
- The production game does not depend on football-data.org during active play.
- No changes are made to the Poké-Guesser project.