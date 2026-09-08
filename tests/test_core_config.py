"""Covers the fail-closed reads of core/config.py."""

import pytest

from core.config import (
    DATABASE_URL_ENV_VAR,
    DEFAULT_MESSAGE_RETENTION_DAYS,
    MESSAGE_RETENTION_DAYS_ENV_VAR,
    TEST_DATABASE_URL_ENV_VAR,
    MisconfiguredError,
    database_url,
    harness_database_url,
    message_retention_days,
)

A_URL = "postgresql+asyncpg://user:pass@127.0.0.1:5432/db"


@pytest.mark.parametrize("value", ["", None])
def test_database_url_refuses_to_guess(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """An unset URL raises instead of falling back to something plausible.

    A default pointing at localhost would let the bot start, connect to nothing and
    look like a Discord problem.
    """
    if value is None:
        monkeypatch.delenv(DATABASE_URL_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(DATABASE_URL_ENV_VAR, value)

    with pytest.raises(MisconfiguredError, match=DATABASE_URL_ENV_VAR):
        database_url()


def test_database_url_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """No import-time capture: a test can repoint the URL without reloading anything."""
    monkeypatch.setenv(DATABASE_URL_ENV_VAR, A_URL)
    assert database_url() == A_URL


def test_the_testing_url_does_not_fall_back_to_the_real_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The absence of a fallback is the point: it is what protects the dev database.

    The harness migrates whatever TEST_DATABASE_URL names. Were it to default to
    DATABASE_URL, a stray `pytest` would migrate and truncate the database being
    developed against, and the symptom would look like data loss rather than a missing
    variable.
    """
    monkeypatch.setenv(DATABASE_URL_ENV_VAR, A_URL)
    monkeypatch.delenv(TEST_DATABASE_URL_ENV_VAR, raising=False)

    with pytest.raises(MisconfiguredError, match=TEST_DATABASE_URL_ENV_VAR):
        harness_database_url()


def test_retention_defaults_to_ninety_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retention has a safe default, unlike the URLs: 90 days keeps data, not deletes it."""
    monkeypatch.delenv(MESSAGE_RETENTION_DAYS_ENV_VAR, raising=False)
    assert message_retention_days() == DEFAULT_MESSAGE_RETENTION_DAYS


def test_retention_reads_an_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MESSAGE_RETENTION_DAYS_ENV_VAR, "30")
    assert message_retention_days() == 30


@pytest.mark.parametrize("value", ["", "  ", "ninety", "30d", "1.5"])
def test_retention_rejects_what_is_not_an_integer(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A typo must not become a number.

    The empty string is the interesting case: `MESSAGE_RETENTION_DAYS=` in a .env is a
    plausible accident, and it takes the default rather than raising.
    """
    monkeypatch.setenv(MESSAGE_RETENTION_DAYS_ENV_VAR, value)

    if value == "":
        assert message_retention_days() == DEFAULT_MESSAGE_RETENTION_DAYS
        return

    with pytest.raises(MisconfiguredError, match=MESSAGE_RETENTION_DAYS_ENV_VAR):
        message_retention_days()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_retention_rejects_a_window_that_purges_everything(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """0 would purge messages as fast as they are ingested, silently."""
    monkeypatch.setenv(MESSAGE_RETENTION_DAYS_ENV_VAR, value)

    with pytest.raises(MisconfiguredError, match=MESSAGE_RETENTION_DAYS_ENV_VAR):
        message_retention_days()
