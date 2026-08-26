from __future__ import annotations

import pytest

from server.config import Settings


def test_production_requires_an_explicit_session_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREST_QUEST_ENV", "production")
    monkeypatch.delenv("CREST_QUEST_SESSION_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="SESSION_SECRET must be at least 32"):
        Settings.from_environment()


def test_production_uses_secure_session_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREST_QUEST_ENV", "production")
    monkeypatch.setenv(
        "CREST_QUEST_SESSION_SECRET", "production-test-secret-at-least-32-chars"
    )

    settings = Settings.from_environment()

    assert settings.session_secret == "production-test-secret-at-least-32-chars"
    assert settings.secure_cookies is True


def test_environment_is_normalized_and_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREST_QUEST_ENV", "  TeSt  ")
    assert Settings.from_environment().environment == "test"

    monkeypatch.setenv("CREST_QUEST_ENV", "staging")
    with pytest.raises(RuntimeError, match="ENV must be one of"):
        Settings.from_environment()


def test_production_rejects_short_session_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREST_QUEST_ENV", " PRODUCTION ")
    monkeypatch.setenv("CREST_QUEST_SESSION_SECRET", "too-short")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        Settings.from_environment()
