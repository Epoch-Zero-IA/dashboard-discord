"""Environment-driven configuration for the shared data layer.

Every value is read at call time, not at import time. Two reasons, both learned from
`backend.security`: the Alembic and pytest entry points import this module long before
a database is needed, and tests must be able to point a single process at another
database without reloading anything.
"""

import os

DATABASE_URL_ENV_VAR = "DATABASE_URL"
TEST_DATABASE_URL_ENV_VAR = "TEST_DATABASE_URL"
MESSAGE_RETENTION_DAYS_ENV_VAR = "MESSAGE_RETENTION_DAYS"

DEFAULT_MESSAGE_RETENTION_DAYS = 90


class MisconfiguredError(Exception):
    """Raised when the environment cannot support a database connection.

    Deliberately a plain exception: `core` knows nothing of Litestar, so it cannot
    raise `ImproperlyConfiguredException` the way `backend.security` does. The API and
    the worker each translate it into their own failure mode — a refused startup for
    the former, a non-zero exit for the latter.
    """


def database_url() -> str:
    """Return the application database URL.

    Returns:
        The URL, in the async form (`postgresql+asyncpg://...`).

    Raises:
        MisconfiguredError: If the variable is unset or empty.
    """
    url = os.getenv(DATABASE_URL_ENV_VAR)
    if not url:
        msg = (
            f"{DATABASE_URL_ENV_VAR} is unset or empty. Set it in the environment "
            f"(.env locally, project variables on Coolify). Both the API and the bot "
            f"need it; the bot has nowhere to put a message without it."
        )
        raise MisconfiguredError(msg)
    return url


def harness_database_url() -> str:
    """Return the URL of the throwaway database used by the `db`-marked tests.

    Named for the harness rather than for the tests: pytest collects module-level
    callables matching `test*` — the prefix, not `test_` — so both `test_database_url`
    and `testing_database_url` get run as tests by any test module that imports them,
    and fail there having no fixture to give them a database.

    There is no fallback to `DATABASE_URL` on purpose. A helpful default would let a
    stray `pytest` migrate and truncate the development database, and the failure
    would look like data loss rather than a missing variable.

    Returns:
        The URL, in the async form (`postgresql+asyncpg://...`).

    Raises:
        MisconfiguredError: If the variable is unset or empty.
    """
    url = os.getenv(TEST_DATABASE_URL_ENV_VAR)
    if not url:
        msg = (
            f"{TEST_DATABASE_URL_ENV_VAR} is unset or empty. Set it in .env (see "
            f".env.example) — it must name a database of its own, never the one "
            f"{DATABASE_URL_ENV_VAR} points at, since the test harness migrates it."
        )
        raise MisconfiguredError(msg)
    return url


def message_retention_days() -> int:
    """Return how long raw message rows are kept before the nightly purge.

    Retention is a policy decision, so it lives in the environment rather than in a
    constant. The aggregate in `daily_activity` outlives the purge, which is what lets
    the dashboard keep its history without keeping the conversations.

    Returns:
        The retention window in days.

    Raises:
        MisconfiguredError: If the value is not a positive integer. A typo must not
            silently become 0 and purge everything on the next run.
    """
    raw = os.getenv(MESSAGE_RETENTION_DAYS_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_MESSAGE_RETENTION_DAYS

    try:
        days = int(raw)
    except ValueError as exc:
        msg = f"{MESSAGE_RETENTION_DAYS_ENV_VAR} must be an integer, got {raw!r}."
        raise MisconfiguredError(msg) from exc

    if days < 1:
        msg = (
            f"{MESSAGE_RETENTION_DAYS_ENV_VAR} must be at least 1, got {days}. "
            f"A zero or negative window would purge messages as fast as they arrive."
        )
        raise MisconfiguredError(msg)
    return days
