"""The worker's healthcheck: `python -m bot.healthcheck`.

Reads the heartbeat from the database rather than answering on a port, because that is
the only check that proves the three things at once — the process lives, the gateway
connection is up, and Postgres is reachable. An HTTP endpoint here would prove the
first and lie about the rest.

Exit code 0 means healthy, anything else unhealthy, which is what Docker reads.
"""

import asyncio
import datetime as dt
import sys

from bot.config import HEARTBEAT_SECONDS
from core.config import database_url
from core.db import create_engine, create_session_factory
from core.queries import last_heartbeat

# Three missed beats. Tight enough that a hung worker is spotted within two minutes,
# loose enough that one slow write does not restart a healthy one.
MAX_BEAT_AGE = dt.timedelta(seconds=3 * HEARTBEAT_SECONDS)

EXIT_HEALTHY = 0
EXIT_UNHEALTHY = 1


def is_fresh(
    beat_at: dt.datetime | None,
    now: dt.datetime,
    max_age: dt.timedelta = MAX_BEAT_AGE,
) -> bool:
    """Report whether a heartbeat is recent enough to call the worker healthy.

    A missing heartbeat is not fresh: on a first start that is true for a few seconds,
    which is what the healthcheck's `start_period` is for.

    Args:
        beat_at: When the worker last beat, None when it never has.
        now: The current instant.
        max_age: How old a beat may be.

    Returns:
        True when the worker is healthy.
    """
    if beat_at is None:
        return False
    return now - beat_at <= max_age


async def check() -> bool:
    """Read the heartbeat and judge it.

    Returns:
        True when the worker is healthy.
    """
    engine = create_engine(database_url())
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            heartbeat = await last_heartbeat(session)
    finally:
        await engine.dispose()

    return is_fresh(heartbeat.beat_at if heartbeat else None, dt.datetime.now(dt.UTC))


def main() -> int:
    """Run the check.

    Every failure is unhealthy, including a misconfiguration, which is why the catch is
    blind: from the outside, a worker that cannot reach its database is
    indistinguishable from one that is not running, and both want a restart. Narrowing
    the exception would let an unforeseen one crash the healthcheck itself and be
    reported as a check error rather than as an unhealthy container.

    Returns:
        0 when healthy, 1 otherwise.
    """
    try:
        healthy = asyncio.run(check())
    except Exception:  # noqa: BLE001 — see the docstring: any failure is unhealthy
        return EXIT_UNHEALTHY
    return EXIT_HEALTHY if healthy else EXIT_UNHEALTHY


if __name__ == "__main__":
    sys.exit(main())
