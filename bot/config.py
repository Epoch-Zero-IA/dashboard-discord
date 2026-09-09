"""The worker's own configuration. The database lives in `core.config`."""

import os

from core.config import MisconfiguredError

DISCORD_TOKEN_ENV_VAR = "DISCORD_TOKEN"
HEARTBEAT_SECONDS = 30


def discord_token() -> str:
    """Return the bot token.

    Same regime as the API key: fail closed. A missing token must stop the process,
    not start a worker that connects to nothing and looks like a Discord outage.

    The error message names the variable and never its value — a token in a log is a
    token to rotate.

    Returns:
        The token.

    Raises:
        MisconfiguredError: If the variable is unset or empty.
    """
    token = os.getenv(DISCORD_TOKEN_ENV_VAR)
    if not token:
        msg = (
            f"{DISCORD_TOKEN_ENV_VAR} is unset or empty. Set it in the environment "
            f"(.env locally, project variables on Coolify). Without it the worker has "
            f"no gateway to connect to."
        )
        raise MisconfiguredError(msg)
    return token


NIGHTLY_HOUR_ENV_VAR = "NIGHTLY_HOUR_UTC"
DEFAULT_NIGHTLY_HOUR = 3


def nightly_hour() -> int:
    """Return the UTC hour the nightly job runs at.

    An environment variable and not a constant, for the same reason as the retention
    window: what hour is quiet is a property of the server, not of the code.

    Returns:
        The hour, 0-23.

    Raises:
        MisconfiguredError: If the value is not an hour. A typo must not silently
            become midnight.
    """
    raw = os.getenv(NIGHTLY_HOUR_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_NIGHTLY_HOUR

    try:
        hour = int(raw)
    except ValueError as exc:
        msg = (
            f"{NIGHTLY_HOUR_ENV_VAR} must be an integer between 0 and 23, got {raw!r}."
        )
        raise MisconfiguredError(msg) from exc

    if not 0 <= hour <= 23:
        msg = f"{NIGHTLY_HOUR_ENV_VAR} must be between 0 and 23, got {hour}."
        raise MisconfiguredError(msg)
    return hour
