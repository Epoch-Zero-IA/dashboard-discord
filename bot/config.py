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
