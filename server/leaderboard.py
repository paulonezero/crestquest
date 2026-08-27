from __future__ import annotations

import math
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from server.models import SUPPORTED_DURATIONS, SUPPORTED_SCOPES, LeagueScope

TOP_ENTRY_LIMIT = 10

_SCOPE_CHECK_VALUES = ",\n            ".join(
    f"'{scope}'" for scope in SUPPORTED_SCOPES
)
_DURATION_CHECK_VALUES = ", ".join(str(duration) for duration in SUPPORTED_DURATIONS)

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS leaderboard_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    username TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score > 0),
    clubs_named INTEGER NOT NULL CHECK (clubs_named >= 0),
    incorrect_selections INTEGER NOT NULL CHECK (incorrect_selections >= 0),
    best_streak INTEGER NOT NULL CHECK (best_streak >= 0),
    clean_three_bonuses INTEGER NOT NULL CHECK (clean_three_bonuses >= 0),
    flawless_multiplier REAL NOT NULL CHECK (flawless_multiplier >= 0),
    scope TEXT NOT NULL CHECK (
        scope IN (
            {_SCOPE_CHECK_VALUES}
        )
    ),
    duration INTEGER NOT NULL CHECK (duration IN ({_DURATION_CHECK_VALUES})),
    submitted_at TEXT NOT NULL
)
"""

_CREATE_ROUND_ID_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS leaderboard_round_identity
ON leaderboard_entries (round_id)
"""

_CREATE_RANKING_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS leaderboard_board_ranking
ON leaderboard_entries (
    scope,
    duration,
    score DESC,
    clubs_named DESC,
    incorrect_selections ASC,
    best_streak DESC,
    submitted_at ASC,
    id ASC
)
"""

_ENTRY_COLUMNS = """
    id,
    round_id,
    player_id,
    username,
    score,
    clubs_named,
    incorrect_selections,
    best_streak,
    clean_three_bonuses,
    flawless_multiplier,
    scope,
    duration,
    submitted_at
"""

_TOP_ENTRIES_SQL = f"""
SELECT {_ENTRY_COLUMNS}
FROM leaderboard_entries
WHERE scope = ? AND duration = ?
ORDER BY
    score DESC,
    clubs_named DESC,
    incorrect_selections ASC,
    best_streak DESC,
    submitted_at ASC,
    id ASC
LIMIT ?
"""

_ENTRY_BY_ROUND_ID_SQL = f"""
SELECT {_ENTRY_COLUMNS}
FROM leaderboard_entries
WHERE round_id = ?
"""


class LeaderboardError(RuntimeError):
    """Base error for leaderboard persistence failures."""


class LeaderboardInitializationError(LeaderboardError):
    """Raised when the leaderboard database cannot be initialized."""


class LeaderboardReadError(LeaderboardError):
    """Raised when leaderboard entries cannot be loaded."""


class LeaderboardWriteError(LeaderboardError):
    """Raised when an entry cannot be saved and the submission may be retried."""


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    round_id: str
    player_id: str
    username: str
    score: int
    clubs_named: int
    incorrect_selections: int
    best_streak: int
    clean_three_bonuses: int
    flawless_multiplier: float
    scope: LeagueScope
    duration: int
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    reached_top_10: bool
    top_entries: tuple[LeaderboardEntry, ...]


class SQLiteLeaderboard:
    """Persistent top-ten views over every qualifying completed round."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._database_path: str = str(database_path)
        self._timeout_seconds: float = timeout_seconds
        self._lock: threading.RLock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        self._prepare_parent_directory(database_path)
        if self._database_path == ":memory:":
            self._memory_connection = self._open_connection()
        self._initialize()

    def submit(self, entry: LeaderboardEntry) -> SubmissionResult:
        """Idempotently persist one completed round and return its board ranking."""

        entry = self._validated_entry(entry)
        if entry.score <= 0:
            return SubmissionResult(
                reached_top_10=False,
                top_entries=self.get_top(entry.scope, entry.duration),
            )

        submitted_at = _serialize_datetime(entry.submitted_at)
        try:
            with self._lock, self._connection() as connection, connection:
                connection.execute(
                    """
                    INSERT INTO leaderboard_entries (
                        round_id,
                        player_id,
                        username,
                        score,
                        clubs_named,
                        incorrect_selections,
                        best_streak,
                        clean_three_bonuses,
                        flawless_multiplier,
                        scope,
                        duration,
                        submitted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(round_id) DO NOTHING
                    """,
                    (
                        entry.round_id,
                        entry.player_id,
                        entry.username,
                        entry.score,
                        entry.clubs_named,
                        entry.incorrect_selections,
                        entry.best_streak,
                        entry.clean_three_bonuses,
                        entry.flawless_multiplier,
                        entry.scope,
                        entry.duration,
                        submitted_at,
                    ),
                )
                saved_row = connection.execute(
                    _ENTRY_BY_ROUND_ID_SQL, (entry.round_id,)
                ).fetchone()
                if saved_row is None:
                    raise sqlite3.IntegrityError(
                        "round submission was not present after insert"
                    )
                saved_entry = _entry_from_row(saved_row)
                if saved_entry != entry:
                    raise LeaderboardWriteError(
                        "The round submission identity conflicts with saved data."
                    )
                saved_id = int(saved_row["id"])
                ranked_rows = connection.execute(
                    _TOP_ENTRIES_SQL,
                    (entry.scope, entry.duration, TOP_ENTRY_LIMIT),
                ).fetchall()
        except LeaderboardWriteError:
            raise
        except sqlite3.Error as error:
            raise LeaderboardWriteError(
                "Could not save the leaderboard entry; retry the submission."
            ) from error

        return SubmissionResult(
            reached_top_10=any(int(row["id"]) == saved_id for row in ranked_rows),
            top_entries=tuple(_entry_from_row(row) for row in ranked_rows),
        )

    def get_top(
        self,
        scope: LeagueScope | str,
        duration: int,
    ) -> tuple[LeaderboardEntry, ...]:
        """Return up to ten ranked entries for one scope and duration."""

        validated_scope, validated_duration = _validate_board(scope, duration)
        try:
            with self._lock, self._connection() as connection:
                rows = connection.execute(
                    _TOP_ENTRIES_SQL,
                    (validated_scope, validated_duration, TOP_ENTRY_LIMIT),
                ).fetchall()
        except sqlite3.Error as error:
            raise LeaderboardReadError("Could not load leaderboard entries.") from error
        return tuple(_entry_from_row(row) for row in rows)

    def _initialize(self) -> None:
        try:
            with self._lock, self._connection() as connection, connection:
                connection.execute(_CREATE_TABLE_SQL)
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(leaderboard_entries)"
                    ).fetchall()
                }
                if "round_id" not in columns:
                    connection.execute(
                        "ALTER TABLE leaderboard_entries ADD COLUMN round_id TEXT"
                    )
                connection.execute(
                    """
                    UPDATE leaderboard_entries
                    SET round_id = 'legacy-' || lower(hex(randomblob(16)))
                    WHERE round_id IS NULL OR trim(round_id) = ''
                    """
                )
                table_sql_row = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'leaderboard_entries'
                    """
                ).fetchone()
                table_sql = str(table_sql_row["sql"] or "")
                if any(f"'{scope}'" not in table_sql for scope in SUPPORTED_SCOPES):
                    connection.execute(
                        "DROP INDEX IF EXISTS leaderboard_round_identity"
                    )
                    connection.execute("DROP INDEX IF EXISTS leaderboard_board_ranking")
                    connection.execute(
                        """
                        ALTER TABLE leaderboard_entries
                        RENAME TO leaderboard_entries_legacy
                        """
                    )
                    connection.execute(_CREATE_TABLE_SQL)
                    connection.execute(
                        f"""
                        INSERT INTO leaderboard_entries ({_ENTRY_COLUMNS})
                        SELECT {_ENTRY_COLUMNS}
                        FROM leaderboard_entries_legacy
                        """
                    )
                    connection.execute("DROP TABLE leaderboard_entries_legacy")
                connection.execute(_CREATE_ROUND_ID_INDEX_SQL)
                connection.execute(_CREATE_RANKING_INDEX_SQL)
        except (OSError, sqlite3.Error) as error:
            raise LeaderboardInitializationError(
                "Could not initialize the leaderboard database."
            ) from error

    def _validated_entry(self, entry: LeaderboardEntry) -> LeaderboardEntry:
        if not isinstance(entry, LeaderboardEntry):
            raise TypeError("entry must be a LeaderboardEntry")

        scope, duration = _validate_board(entry.scope, entry.duration)
        if not isinstance(entry.round_id, str) or not entry.round_id.strip():
            raise ValueError("round_id must not be empty")
        if not isinstance(entry.player_id, str) or not entry.player_id.strip():
            raise ValueError("player_id must not be empty")
        if not isinstance(entry.username, str) or not entry.username.strip():
            raise ValueError("username must not be empty")

        for field_name in (
            "score",
            "clubs_named",
            "incorrect_selections",
            "best_streak",
            "clean_three_bonuses",
        ):
            value = getattr(entry, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if field_name != "score" and value < 0:
                raise ValueError(f"{field_name} must not be negative")

        multiplier = entry.flawless_multiplier
        if isinstance(multiplier, bool) or not isinstance(multiplier, int | float):
            raise TypeError("flawless_multiplier must be a number")
        if not math.isfinite(multiplier) or multiplier < 0:
            raise ValueError("flawless_multiplier must be a finite nonnegative number")

        normalized_time = _normalize_datetime(entry.submitted_at)
        return replace(
            entry,
            scope=scope,
            duration=duration,
            flawless_multiplier=float(multiplier),
            submitted_at=normalized_time,
        )

    @staticmethod
    def _prepare_parent_directory(database_path: str | Path) -> None:
        if str(database_path) == ":memory:":
            return
        try:
            Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LeaderboardInitializationError(
                "Could not create the leaderboard database directory."
            ) from error

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=self._timeout_seconds,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._memory_connection or self._open_connection()
        try:
            yield connection
        finally:
            if self._memory_connection is None:
                connection.close()


def _validate_board(scope: str, duration: int) -> tuple[LeagueScope, int]:
    if scope not in SUPPORTED_SCOPES:
        supported = ", ".join(SUPPORTED_SCOPES)
        raise ValueError(
            f"Unsupported leaderboard scope {scope!r}; choose {supported}."
        )
    if isinstance(duration, bool) or duration not in SUPPORTED_DURATIONS:
        supported = ", ".join(str(value) for value in SUPPORTED_DURATIONS)
        raise ValueError(
            f"Unsupported leaderboard duration {duration!r}; choose {supported}."
        )
    return cast(LeagueScope, scope), duration


def _normalize_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("submitted_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_datetime(value: datetime) -> str:
    return _normalize_datetime(value).isoformat(timespec="microseconds")


def _entry_from_row(row: sqlite3.Row) -> LeaderboardEntry:
    return LeaderboardEntry(
        round_id=str(row["round_id"]),
        player_id=str(row["player_id"]),
        username=str(row["username"]),
        score=int(row["score"]),
        clubs_named=int(row["clubs_named"]),
        incorrect_selections=int(row["incorrect_selections"]),
        best_streak=int(row["best_streak"]),
        clean_three_bonuses=int(row["clean_three_bonuses"]),
        flawless_multiplier=float(row["flawless_multiplier"]),
        scope=cast(LeagueScope, row["scope"]),
        duration=int(row["duration"]),
        submitted_at=datetime.fromisoformat(str(row["submitted_at"])),
    )
