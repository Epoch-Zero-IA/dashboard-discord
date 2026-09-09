"""Entry point of the worker: `python -m bot`.

Owns the two things a long-running process must own — the engine's lifetime and the
exit code. Section 3 of the spec makes the exit code load-bearing: a non-zero code is
how a database outage gets fixed, since the orchestrator restarts us and the catch-up
cursors refill the gap. There is no memory buffer to lose.
"""

import asyncio
import logging
import sys

import structlog

from bot.config import discord_token
from bot.runner import EXIT_MISCONFIGURED, Worker
from core.config import MisconfiguredError, database_url
from core.db import create_engine, create_session_factory

log = structlog.get_logger(__name__)

BACKFILL_COMMAND = "backfill"


def configure_logging() -> None:
    """Set up structlog: coloured console on a TTY, JSON otherwise.

    Same split as the API, and for the same reason: production logs go straight to a
    collector without being re-parsed. No log ever carries the Discord token — the
    configuration is never logged as an object, only variable names are.
    """
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    renderer = (
        structlog.dev.ConsoleRenderer()
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
    )


async def run(*, backfill: bool = False) -> int:
    """Connect, ingest until stopped, and report how it ended.

    Args:
        backfill: Run the history import to completion and exit, rather than listening
            to the gateway indefinitely.

    Returns:
        The exit code: 0 on a clean stop, 1 when the database went away, 2 when the
        environment is wrong.
    """
    token = discord_token()
    # Read before connecting: a missing DATABASE_URL must not cost a gateway session,
    # and it is the one error a restart will never fix.
    engine = create_engine(database_url())
    worker = Worker(create_session_factory(engine), backfill=backfill)
    try:
        await worker.start(token)
    finally:
        if not worker.is_closed():
            await worker.close()
        await engine.dispose()
    return worker.exit_code


def main() -> int:
    """Run the worker, translating a configuration error into an exit code.

    Returns:
        The process exit code.
    """
    configure_logging()
    # One optional argument, so `argparse` would be three times the code: `backfill`
    # runs the import to completion, anything else listens.
    backfill = len(sys.argv) > 1 and sys.argv[1] == BACKFILL_COMMAND
    try:
        return asyncio.run(run(backfill=backfill))
    except MisconfiguredError as exc:
        # Logged rather than raised: a traceback here points at our own `raise`, while
        # the message names the variable to set, which is the only actionable part.
        log.error("misconfigured", error=str(exc))
        return EXIT_MISCONFIGURED
    except KeyboardInterrupt:
        log.info("interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
