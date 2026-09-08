"""The token, and its fail-closed regime."""

import pytest

from bot.config import (
    DEFAULT_NIGHTLY_HOUR,
    DISCORD_TOKEN_ENV_VAR,
    NIGHTLY_HOUR_ENV_VAR,
    discord_token,
    nightly_hour,
)
from core.config import MisconfiguredError


def test_a_missing_token_stops_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed, like the API key: no token means no start.

    The alternative is a process that lives, connects to nothing and looks like a
    Discord outage.
    """
    monkeypatch.delenv(DISCORD_TOKEN_ENV_VAR, raising=False)

    with pytest.raises(MisconfiguredError, match=DISCORD_TOKEN_ENV_VAR):
        discord_token()


def test_the_error_never_quotes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty token is still a misconfiguration, and the message names the variable.

    A token that reaches a log is a token to rotate, so the message may name the
    variable but never its value.
    """
    monkeypatch.setenv(DISCORD_TOKEN_ENV_VAR, "")

    with pytest.raises(MisconfiguredError) as raised:
        discord_token()

    assert DISCORD_TOKEN_ENV_VAR in str(raised.value)


def test_the_token_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISCORD_TOKEN_ENV_VAR, "a-token")
    assert discord_token() == "a-token"


def test_the_nightly_hour_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NIGHTLY_HOUR_ENV_VAR, raising=False)
    assert nightly_hour() == DEFAULT_NIGHTLY_HOUR


def test_the_nightly_hour_reads_an_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NIGHTLY_HOUR_ENV_VAR, "23")
    assert nightly_hour() == 23


@pytest.mark.parametrize("value", ["minuit", "3h", "3.5"])
def test_an_hour_that_is_not_a_number_raises(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A typo must not silently become midnight."""
    monkeypatch.setenv(NIGHTLY_HOUR_ENV_VAR, value)

    with pytest.raises(MisconfiguredError, match=NIGHTLY_HOUR_ENV_VAR):
        nightly_hour()


@pytest.mark.parametrize("value", ["-1", "24", "99"])
def test_an_hour_outside_the_clock_raises(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """`tasks.loop` would otherwise raise at startup, much further from the cause."""
    monkeypatch.setenv(NIGHTLY_HOUR_ENV_VAR, value)

    with pytest.raises(MisconfiguredError, match=NIGHTLY_HOUR_ENV_VAR):
        nightly_hour()
