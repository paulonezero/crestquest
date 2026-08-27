from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import cast

import pytest

from server.leaderboard import (
    LeaderboardEntry,
    LeaderboardWriteError,
    SQLiteLeaderboard,
)
from server.models import SUPPORTED_DURATIONS, SUPPORTED_SCOPES, LeagueScope

SUBMITTED_AT = datetime(2026, 1, 2, 12, tzinfo=UTC)


def entry(
    username: str,
    *,
    player_id: str | None = None,
    round_id: str | None = None,
    score: int = 100,
    clubs_named: int = 5,
    incorrect_selections: int = 1,
    best_streak: int = 3,
    clean_three_bonuses: int = 1,
    flawless_multiplier: float = 1.0,
    scope: str = "all",
    duration: int = 30,
    submitted_at: datetime = SUBMITTED_AT,
) -> LeaderboardEntry:
    return LeaderboardEntry(
        round_id=round_id or f"round-{username}",
        player_id=player_id or f"player-{username}",
        username=username,
        score=score,
        clubs_named=clubs_named,
        incorrect_selections=incorrect_selections,
        best_streak=best_streak,
        clean_three_bonuses=clean_three_bonuses,
        flawless_multiplier=flawless_multiplier,
        scope=cast(LeagueScope, scope),
        duration=duration,
        submitted_at=submitted_at,
    )


def test_all_27_scope_and_duration_boards_are_separate(tmp_path: Path) -> None:
    leaderboard = SQLiteLeaderboard(tmp_path / "leaderboard.sqlite3")

    for index, (scope, duration) in enumerate(
        product(SUPPORTED_SCOPES, SUPPORTED_DURATIONS)
    ):
        leaderboard.submit(
            entry(
                f"board-{index}",
                score=index + 1,
                scope=scope,
                duration=duration,
            )
        )

    boards = [
        leaderboard.get_top(scope, duration)
        for scope, duration in product(SUPPORTED_SCOPES, SUPPORTED_DURATIONS)
    ]
    assert len(boards) == 27
    assert all(len(board) == 1 for board in boards)
    assert {board[0].username for board in boards} == {
        f"board-{index}" for index in range(27)
    }


def test_ranking_uses_every_tie_breaker_in_order(tmp_path: Path) -> None:
    leaderboard = SQLiteLeaderboard(tmp_path / "leaderboard.sqlite3")
    submissions = [
        entry("lower-score", score=99, clubs_named=20),
        entry("later", submitted_at=SUBMITTED_AT + timedelta(seconds=1)),
        entry("same-tie-first"),
        entry("same-tie-second"),
        entry("earlier", submitted_at=SUBMITTED_AT - timedelta(seconds=1)),
        entry("higher-streak", best_streak=4),
        entry("fewer-incorrect", incorrect_selections=0, best_streak=0),
        entry("more-clubs", clubs_named=6, incorrect_selections=20, best_streak=0),
        entry("higher-score", score=101, clubs_named=0),
    ]

    for submission in submissions:
        leaderboard.submit(submission)

    assert [item.username for item in leaderboard.get_top("all", 30)] == [
        "higher-score",
        "more-clubs",
        "fewer-incorrect",
        "higher-streak",
        "earlier",
        "same-tie-first",
        "same-tie-second",
        "later",
        "lower-score",
    ]


def test_top_ten_result_reports_whether_new_entry_is_ranked(tmp_path: Path) -> None:
    database_path = tmp_path / "leaderboard.sqlite3"
    leaderboard = SQLiteLeaderboard(database_path)
    for score in range(20, 10, -1):
        leaderboard.submit(entry(f"existing-{score}", score=score))

    missed = leaderboard.submit(entry("missed", score=1))
    reached = leaderboard.submit(entry("reached", score=21))

    assert missed.reached_top_10 is False
    assert len(missed.top_entries) == 10
    assert reached.reached_top_10 is True
    assert len(reached.top_entries) == 10
    assert reached.top_entries[0].username == "reached"
    assert "missed" not in {item.username for item in reached.top_entries}
    with sqlite3.connect(database_path) as connection:
        saved_count = connection.execute(
            "SELECT COUNT(*) FROM leaderboard_entries"
        ).fetchone()
    assert saved_count == (12,)


def test_same_player_can_have_multiple_saved_entries(tmp_path: Path) -> None:
    leaderboard = SQLiteLeaderboard(tmp_path / "leaderboard.sqlite3")
    first = entry("Alex", player_id="stable-browser-id", score=40)
    second = replace(
        first,
        round_id="repeat-player-round-2",
        score=60,
        submitted_at=SUBMITTED_AT + timedelta(seconds=1),
    )

    leaderboard.submit(first)
    leaderboard.submit(second)

    board = leaderboard.get_top("all", 30)
    assert [item.score for item in board] == [60, 40]
    assert [item.player_id for item in board] == [
        "stable-browser-id",
        "stable-browser-id",
    ]


def test_entries_persist_when_store_is_reopened(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "leaderboard.sqlite3"
    original = entry(
        "Persistent Player",
        clean_three_bonuses=2,
        flawless_multiplier=2.0,
    )
    SQLiteLeaderboard(database_path).submit(original)

    reopened = SQLiteLeaderboard(database_path)

    assert reopened.get_top("all", 30) == (original,)


def test_invalid_boards_are_rejected(tmp_path: Path) -> None:
    leaderboard = SQLiteLeaderboard(tmp_path / "leaderboard.sqlite3")

    with pytest.raises(ValueError, match="Unsupported leaderboard scope"):
        leaderboard.get_top("champions-league", 30)
    with pytest.raises(ValueError, match="Unsupported leaderboard duration"):
        leaderboard.get_top("all", 45)
    with pytest.raises(ValueError, match="Unsupported leaderboard scope"):
        leaderboard.submit(entry("invalid", scope="champions-league"))
    with pytest.raises(ValueError, match="Unsupported leaderboard duration"):
        leaderboard.submit(entry("invalid", duration=45))


def test_nonpositive_scores_are_not_stored(tmp_path: Path) -> None:
    leaderboard = SQLiteLeaderboard(tmp_path / "leaderboard.sqlite3")
    positive = leaderboard.submit(entry("positive", score=1))
    zero = leaderboard.submit(entry("zero", score=0))
    negative = leaderboard.submit(entry("negative", score=-1))

    assert positive.reached_top_10 is True
    assert zero.reached_top_10 is False
    assert negative.reached_top_10 is False
    assert [item.username for item in leaderboard.get_top("all", 30)] == ["positive"]


def test_duplicate_round_identity_is_idempotent_and_conflicts_are_rejected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "leaderboard.sqlite3"
    leaderboard = SQLiteLeaderboard(database_path)
    submission = entry("Retry", round_id="stable-round-id")

    first = leaderboard.submit(submission)
    duplicate = leaderboard.submit(submission)

    assert first == duplicate
    assert leaderboard.get_top("all", 30) == (submission,)
    with sqlite3.connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM leaderboard_entries WHERE round_id = ?",
            (submission.round_id,),
        ).fetchone()
    assert count == (1,)

    with pytest.raises(LeaderboardWriteError, match="identity conflicts"):
        leaderboard.submit(replace(submission, score=submission.score + 1))


def test_existing_database_is_migrated_with_unique_round_identities(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "leaderboard.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE leaderboard_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id TEXT NOT NULL,
                username TEXT NOT NULL,
                score INTEGER NOT NULL,
                clubs_named INTEGER NOT NULL,
                incorrect_selections INTEGER NOT NULL,
                best_streak INTEGER NOT NULL,
                clean_three_bonuses INTEGER NOT NULL,
                flawless_multiplier REAL NOT NULL,
                scope TEXT NOT NULL,
                duration INTEGER NOT NULL,
                submitted_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO leaderboard_entries (
                player_id, username, score, clubs_named,
                incorrect_selections, best_streak, clean_three_bonuses,
                flawless_multiplier, scope, duration, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-player",
                "Legacy",
                10,
                1,
                0,
                1,
                0,
                1.0,
                "all",
                30,
                SUBMITTED_AT.isoformat(),
            ),
        )

    leaderboard = SQLiteLeaderboard(database_path)
    leaderboard.submit(
        entry("New", round_id="new-round", scope="championship")
    )

    with sqlite3.connect(database_path) as connection:
        round_ids = connection.execute(
            "SELECT round_id FROM leaderboard_entries ORDER BY id"
        ).fetchall()
        indexes = connection.execute(
            "PRAGMA index_list(leaderboard_entries)"
        ).fetchall()
    assert round_ids[0][0].startswith("legacy-")
    assert round_ids[1] == ("new-round",)
    assert leaderboard.get_top("championship", 30)[0].round_id == "new-round"
    assert any(row[1] == "leaderboard_round_identity" and row[2] for row in indexes)


def test_failed_write_raises_retryable_error_without_partial_save(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "leaderboard.sqlite3"
    leaderboard = SQLiteLeaderboard(database_path)
    leaderboard.submit(entry("already-saved", score=10))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER simulate_leaderboard_write_failure
            BEFORE INSERT ON leaderboard_entries
            BEGIN
                SELECT RAISE(ABORT, 'simulated write failure');
            END
            """
        )

    with pytest.raises(LeaderboardWriteError, match="retry") as captured:
        leaderboard.submit(entry("retry-me", score=20))

    assert isinstance(captured.value.__cause__, sqlite3.IntegrityError)
    assert [item.username for item in leaderboard.get_top("all", 30)] == [
        "already-saved"
    ]
