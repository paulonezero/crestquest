from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    session_secret: str
    frontend_dist: Path
    data_path: Path
    leaderboard_path: Path

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.getenv("CREST_QUEST_ENV", "development").strip().lower()
        session_secret = os.getenv("CREST_QUEST_SESSION_SECRET", "")

        allowed_environments = {"development", "test", "production"}
        if environment not in allowed_environments:
            allowed = ", ".join(sorted(allowed_environments))
            raise RuntimeError(
                f"CREST_QUEST_ENV must be one of {allowed}; got {environment!r}"
            )
        if environment == "production" and len(session_secret) < 32:
            raise RuntimeError(
                "CREST_QUEST_SESSION_SECRET must be at least 32 characters "
                "in production"
            )

        return cls(
            environment=environment,
            session_secret=session_secret or "local-development-only-secret",
            frontend_dist=Path(
                os.getenv(
                    "CREST_QUEST_FRONTEND_DIST", PROJECT_ROOT / "frontend" / "dist"
                )
            ),
            data_path=Path(
                os.getenv("CREST_QUEST_DATA_PATH", PROJECT_ROOT / "data" / "clubs.json")
            ),
            leaderboard_path=Path(
                os.getenv(
                    "CREST_QUEST_LEADERBOARD_PATH",
                    PROJECT_ROOT / "var" / "leaderboard.sqlite3",
                )
            ),
        )

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production"
