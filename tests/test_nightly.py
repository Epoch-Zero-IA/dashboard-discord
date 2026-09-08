"""The nightly job: which days it deals with, and what it does to them."""

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bot.nightly import days_to_purge, run_nightly
from core.aggregation import (
    aggregate_day,
    day_bounds,
    day_is_aggregated,
    oldest_message_day,
    purge_day,
)
from core.models import DailyActivity, Message
from core.upserts import ingest_message, mark_message_deleted
from tests.factories import CHANNEL_ID, an_event

TODAY = dt.date(2026, 9, 8)
YESTERDAY = TODAY - dt.timedelta(days=1)


def at(day: dt.date, hour: int = 12) -> dt.datetime:
    """Return an aware UTC instant inside `day`.

    Args:
        day: The day to place the instant in.
        hour: The hour, UTC.

    Returns:
        The instant.
    """
    return dt.datetime.combine(day, dt.time(hour=hour), tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Tier one: the arithmetic. No database.
# ---------------------------------------------------------------------------


def test_a_day_is_bounded_half_open_in_utc() -> None:
    """Half-open, so two consecutive days share no instant and count no message twice."""
    start, end = day_bounds(TODAY)

    assert start == dt.datetime(2026, 9, 8, tzinfo=dt.UTC)
    assert end == dt.datetime(2026, 9, 9, tzinfo=dt.UTC)


def test_no_messages_means_nothing_to_purge() -> None:
    assert days_to_purge(None, TODAY) == []


def test_messages_inside_the_window_are_left_alone() -> None:
    """The cutoff is the first day to keep, so a message on it survives."""
    assert days_to_purge(TODAY, TODAY) == []
    assert days_to_purge(TODAY + dt.timedelta(days=1), TODAY) == []


def test_every_day_before_the_cutoff_is_purged_oldest_first() -> None:
    oldest = dt.date(2026, 9, 1)

    days = days_to_purge(oldest, dt.date(2026, 9, 4))

    assert days == [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]


def test_the_budget_caps_one_run() -> None:
    """A first run on a database older than the window must not delete months at once.

    The rest is taken by the next night: the work is replayable, so a budget costs
    nothing but a delay.
    """
    days = days_to_purge(dt.date(2026, 1, 1), TODAY, budget=3)

    assert days == [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)]


# ---------------------------------------------------------------------------
# Tier two: the aggregation and the purge, on a real engine.
# ---------------------------------------------------------------------------


async def _ingest(
    session: AsyncSession, *, message_id: int, day: dt.date, content: str
) -> None:
    """Ingest one message on `day`.

    Args:
        session: The session to write through.
        message_id: The snowflake to use.
        day: The day to place it on.
        content: Its body.
    """
    await ingest_message(
        session,
        an_event(message_id=message_id, content=content, created_at=at(day)),
    )


@pytest.mark.db
async def test_a_day_is_aggregated_per_channel_and_author(
    db_session: AsyncSession,
) -> None:
    """Two messages, one row, and the characters summed."""
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="douze")
    await _ingest(db_session, message_id=2, day=YESTERDAY, content="sept")

    await aggregate_day(db_session, YESTERDAY)

    row = (await db_session.execute(select(DailyActivity))).scalar_one()
    assert row.activity_date == YESTERDAY
    assert row.channel_id == CHANNEL_ID
    assert row.message_count == 2
    assert row.character_count == len("douze") + len("sept")


@pytest.mark.db
async def test_aggregating_twice_gives_the_same_numbers(
    db_session: AsyncSession,
) -> None:
    """Replayable, which is what lets the job be rerun after any failure."""
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="bonjour")

    await aggregate_day(db_session, YESTERDAY)
    await aggregate_day(db_session, YESTERDAY)

    row = (await db_session.execute(select(DailyActivity))).scalar_one()
    assert row.message_count == 1


@pytest.mark.db
async def test_messages_of_another_day_are_not_counted(
    db_session: AsyncSession,
) -> None:
    """The half-open range, checked where it matters."""
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="hier")
    await _ingest(db_session, message_id=2, day=TODAY, content="aujourd'hui")

    await aggregate_day(db_session, YESTERDAY)

    row = (await db_session.execute(select(DailyActivity))).scalar_one()
    assert row.message_count == 1


@pytest.mark.db
async def test_a_deleted_message_still_counts_but_carries_no_characters(
    db_session: AsyncSession,
) -> None:
    """Section 2: the deletion is honoured, the statistic stays right.

    The content is gone, so a day recomputed after a deletion loses its characters.
    That is the intended trade, not an accident.
    """
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="effacé")
    await mark_message_deleted(db_session, 1, at(TODAY))

    await aggregate_day(db_session, YESTERDAY)

    row = (await db_session.execute(select(DailyActivity))).scalar_one()
    assert row.message_count == 1
    assert row.character_count == 0


@pytest.mark.db
async def test_the_purge_refuses_a_day_that_was_never_aggregated(
    db_session: AsyncSession,
) -> None:
    """The one rule with no recovery if it is broken.

    A purge following a failed aggregation would destroy the day for good, so the
    refusal is a query and not a comment.
    """
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="pas encore compté")

    deleted = await purge_day(db_session, YESTERDAY)

    assert deleted == 0
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 1


@pytest.mark.db
async def test_the_purge_takes_an_aggregated_day_and_keeps_the_aggregate(
    db_session: AsyncSession,
) -> None:
    """The whole point: the history survives, the conversations do not."""
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="bonjour")
    await aggregate_day(db_session, YESTERDAY)

    deleted = await purge_day(db_session, YESTERDAY)

    assert deleted == 1
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 0
    assert await day_is_aggregated(db_session, YESTERDAY) is True


@pytest.mark.db
async def test_aggregating_a_purged_day_does_not_zero_its_history(
    db_session: AsyncSession,
) -> None:
    """The nightly job aggregates a day again right before purging it.

    Once the messages are gone there is nothing left to count, and the recompute must
    leave the aggregate exactly as it was rather than overwrite it with zeroes.
    """
    await _ingest(db_session, message_id=1, day=YESTERDAY, content="bonjour")
    await aggregate_day(db_session, YESTERDAY)
    await purge_day(db_session, YESTERDAY)

    await aggregate_day(db_session, YESTERDAY)

    row = (await db_session.execute(select(DailyActivity))).scalar_one()
    assert row.message_count == 1
    assert row.character_count == len("bonjour")


@pytest.mark.db
async def test_the_oldest_day_is_read_from_the_messages(
    db_session: AsyncSession,
) -> None:
    await _ingest(db_session, message_id=1, day=dt.date(2026, 8, 1), content="vieux")
    await _ingest(db_session, message_id=2, day=YESTERDAY, content="récent")

    assert await oldest_message_day(db_session) == dt.date(2026, 8, 1)


@pytest.mark.db
async def test_a_nightly_run_aggregates_then_purges_past_retention(
    db_engine: AsyncEngine,
) -> None:
    """End to end, with a retention window of two days.

    The old message goes, its count stays, and yesterday's message is untouched — the
    three properties the dashboard depends on.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    old_day = TODAY - dt.timedelta(days=10)
    async with factory() as session:
        await _ingest(session, message_id=1, day=old_day, content="ancien")
        await _ingest(session, message_id=2, day=YESTERDAY, content="hier")
        await session.commit()

    report = await run_nightly(factory, today=TODAY, retention_days=2)

    assert report.purged_messages == 1
    async with factory() as session:
        remaining = (await session.execute(select(Message))).scalars().all()
        assert [message.id for message in remaining] == [2]
        assert await day_is_aggregated(session, old_day) is True
        assert await day_is_aggregated(session, YESTERDAY) is True
