"""The nightly job: aggregate, then purge. In that order, always.

Scheduled inside the worker process, which is the dividend of running a single replica:
no distributed lock, no separate scheduler, no risk of two runs at once. The day the bot
has to scale, this is what has to move out first.

The ordering is not a detail. Aggregating before purging is what lets the dashboard keep
its history; purging a day that was never aggregated would destroy it. `core.aggregation`
enforces the rule in SQL rather than trusting this caller to remember it.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.aggregation import aggregate_day, oldest_message_day, purge_day
from core.config import message_retention_days

log = structlog.get_logger(__name__)

# How many past days one run may purge. A first run on a database older than the
# retention window would otherwise delete months in one transaction and hold the table
# for minutes; the leftover is taken by the next night, since the work is replayable.
PURGE_DAYS_BUDGET = 7


@dataclass(frozen=True, slots=True)
class NightlyReport:
    """What one nightly run did, for the log line and for the tests."""

    aggregated_days: int = 0
    aggregate_rows: int = 0
    purged_days: int = 0
    purged_messages: int = 0


def days_to_purge(
    oldest_day: dt.date | None,
    cutoff: dt.date,
    budget: int = PURGE_DAYS_BUDGET,
) -> list[dt.date]:
    """List the days that are past retention and still hold messages.

    Pure, and the only arithmetic of this module: given how far back the messages go and
    where the retention window starts, which days does this run deal with.

    Args:
        oldest_day: The day of the oldest message held, None when there are none.
        cutoff: The first day to keep. Days strictly before it are purged.
        budget: How many days at most.

    Returns:
        The days to purge, oldest first, at most `budget` of them.
    """
    if oldest_day is None or oldest_day >= cutoff:
        return []

    span = (cutoff - oldest_day).days
    return [
        oldest_day + dt.timedelta(days=offset) for offset in range(min(span, budget))
    ]


async def run_nightly(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    today: dt.date,
    retention_days: int | None = None,
    budget: int = PURGE_DAYS_BUDGET,
) -> NightlyReport:
    """Aggregate the day just ended, then purge what is past retention.

    Each day is handled in its own transaction: a failure halfway through leaves the
    days already done committed, and the next run resumes from the oldest message left.

    Args:
        session_factory: Where each day gets its session.
        today: The current day, passed in rather than read from the clock so a test can
            place itself anywhere in time.
        retention_days: Retention window. Defaults to the environment.
        budget: How many past days this run may purge.

    Returns:
        What the run did.
    """
    retention = (
        retention_days if retention_days is not None else message_retention_days()
    )
    yesterday = today - dt.timedelta(days=1)

    async with session_factory() as session:
        rows = await aggregate_day(session, yesterday)
        await session.commit()

    cutoff = today - dt.timedelta(days=retention)
    async with session_factory() as session:
        oldest = await oldest_message_day(session)

    days: Sequence[dt.date] = days_to_purge(oldest, cutoff, budget)
    purged_days = 0
    purged_messages = 0

    for day in days:
        async with session_factory() as session:
            # Aggregated again right before the purge, and not only the night it
            # happened: a day whose aggregation failed back then would otherwise be
            # refused by `purge_day` forever, and the messages would pile up.
            await aggregate_day(session, day)
            deleted = await purge_day(session, day)
            await session.commit()

        if deleted:
            purged_days += 1
            purged_messages += deleted

    report = NightlyReport(
        aggregated_days=1 + len(days),
        aggregate_rows=rows,
        purged_days=purged_days,
        purged_messages=purged_messages,
    )
    log.info(
        "nightly_done",
        aggregated_days=report.aggregated_days,
        aggregate_rows=report.aggregate_rows,
        purged_days=report.purged_days,
        purged_messages=report.purged_messages,
        retention_days=retention,
    )
    return report
