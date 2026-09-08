"""The nightly aggregation and purge, in SQL that neither engine has to be special for.

Two rules from section 2 of the spec, and the second one is the reason this module has
three small functions rather than one big statement:

- **The aggregate outlives the messages.** `daily_activity` is kept indefinitely, the
  raw rows are purged past `MESSAGE_RETENTION_DAYS`. That is how the dashboard keeps its
  history without keeping the conversations.
- **The purge only ever touches a day that has already been aggregated.** A purge
  following a failed aggregation would destroy the data for good, and it is the one
  mistake here with no recovery.

The day boundaries are computed in Python and applied as a range on `created_at`. No
`date_trunc`, no window function: the aggregation stays replayable and reads the same
whatever the engine.
"""

import datetime as dt

from sqlalchemy import Date, delete, exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import rows_affected
from core.models import DailyActivity, Message

AGGREGATE_COLUMNS = [
    "activity_date",
    "channel_id",
    "author_id",
    "message_count",
    "character_count",
]


def day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Return the half-open UTC range covering `day`.

    UTC for the socle, as recorded in section 4 of the spec: a French evening spills
    over into the next day, and the definitive call belongs to the dashboard. Since the
    aggregate is replayable inside the retention window, the choice is not sealed.

    Args:
        day: The day to bound.

    Returns:
        Its start (inclusive) and the start of the next day (exclusive).
    """
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    return start, start + dt.timedelta(days=1)


async def aggregate_day(session: AsyncSession, day: dt.date) -> int:
    """Recompute `daily_activity` for one day from the messages still held.

    Replayable: running it twice writes the same numbers. Two consequences worth
    knowing, both deliberate:

    - A day whose messages have been purged aggregates **nothing**, so the existing
      aggregate survives untouched. Recomputing a purged day must not zero its history.
    - A deleted message still counts as a message but contributes no characters — its
      content is gone, and honouring the deletion is what section 2 asks for. So a day
      recomputed after a deletion loses characters, on purpose.

    Args:
        session: The session to write through.
        day: The day to recompute.

    Returns:
        How many aggregate rows were written.
    """
    start, end = day_bounds(day)
    source = (
        select(
            literal(day, Date).label("activity_date"),
            Message.channel_id,
            Message.author_id,
            func.count().label("message_count"),
            func.coalesce(
                func.sum(func.length(func.coalesce(Message.content, ""))), 0
            ).label("character_count"),
        )
        .where(Message.created_at >= start, Message.created_at < end)
        .group_by(Message.channel_id, Message.author_id)
    )

    statement = insert(DailyActivity).from_select(AGGREGATE_COLUMNS, source)
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                DailyActivity.activity_date,
                DailyActivity.channel_id,
                DailyActivity.author_id,
            ],
            set_={
                "message_count": statement.excluded.message_count,
                "character_count": statement.excluded.character_count,
            },
        )
    )
    return rows_affected(result)


async def day_is_aggregated(session: AsyncSession, day: dt.date) -> bool:
    """Report whether `daily_activity` already holds anything for `day`.

    Args:
        session: The session to read through.
        day: The day to check.

    Returns:
        True when at least one aggregate row exists.
    """
    return bool(
        await session.scalar(select(exists().where(DailyActivity.activity_date == day)))
    )


async def purge_day(session: AsyncSession, day: dt.date) -> int:
    """Delete the raw messages of `day`, but only once they have been counted.

    The guard is the point of the function. Refusing to purge an unaggregated day makes
    a failed aggregation a delay rather than a data loss, and the check is a query and
    not a comment so that nobody has to remember it.

    Args:
        session: The session to write through.
        day: The day to purge.

    Returns:
        How many message rows were deleted; 0 when the day was not aggregated, which is
        a refusal and not an empty day.
    """
    if not await day_is_aggregated(session, day):
        return 0

    start, end = day_bounds(day)
    result = await session.execute(
        delete(Message).where(Message.created_at >= start, Message.created_at < end)
    )
    return rows_affected(result)


async def oldest_message_day(session: AsyncSession) -> dt.date | None:
    """Return the day of the oldest message still held.

    Read in Python rather than with `date_trunc`, which keeps the query trivial and the
    module free of engine-specific date functions.

    Args:
        session: The session to read through.

    Returns:
        The day, or None when there are no messages at all.
    """
    oldest = await session.scalar(select(func.min(Message.created_at)))
    if oldest is None:
        return None
    return oldest.astimezone(dt.UTC).date()
