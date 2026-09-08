"""The token, and its fail-closed regime."""

import pytest

from bot.config import DISCORD_TOKEN_ENV_VAR, discord_token
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
