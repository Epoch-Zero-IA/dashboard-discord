"""The read side: what the acceptance endpoint is built on."""

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.queries import channel_ingest_status, last_heartbeat
from core.upserts import ingest_message, record_heartbeat, save_cursor
from tests.factories import CHANNEL_ID, POSTED_AT, an_event

OTHER_CHANNEL_ID = CHANNEL_ID + 1


@pytest.mark.db
async def test_the_status_reports_a_channel_with_no_cursor(
    db_session: AsyncSession,
) -> None:
    """A channel ingested live but never caught up belongs in the answer.

    That is precisely the state a reader wants to see: messages are arriving, the
    backfill has not run.
    """
    await ingest_message(db_session, an_event())

    rows = await channel_ingest_status(db_session)

    assert len(rows) == 1
    assert rows[0].channel_id == CHANNEL_ID
    assert rows[0].message_count == 1
    assert rows[0].oldest_message_id is None
    assert rows[0].is_complete is False


@pytest.mark.db
async def test_the_status_joins_the_cursor_and_counts_the_messages(
    db_session: AsyncSession,
) -> None:
    await ingest_message(db_session, an_event(message_id=10))
    await ingest_message(db_session, an_event(message_id=11))
    await save_cursor(
        db_session,
        CHANNEL_ID,
        oldest_message_id=10,
        newest_message_id=11,
        is_complete=True,
    )

    rows = await channel_ingest_status(db_session)

    assert len(rows) == 1
    assert rows[0].message_count == 2
    assert rows[0].oldest_message_id == 10
    assert rows[0].newest_message_id == 11
    assert rows[0].is_complete is True


@pytest.mark.db
async def test_every_channel_is_counted_separately(db_session: AsyncSession) -> None:
    """The outer joins must not multiply the counts across channels.

    The classic failure of a join like this one: two channels of two messages each
    reporting four apiece.
    """
    await ingest_message(db_session, an_event(message_id=10))
    await ingest_message(db_session, an_event(message_id=11))
    await ingest_message(
        db_session, an_event(message_id=20, channel_id=OTHER_CHANNEL_ID)
    )

    rows = await channel_ingest_status(db_session)

    assert [(row.channel_id, row.message_count) for row in rows] == [
        (CHANNEL_ID, 2),
        (OTHER_CHANNEL_ID, 1),
    ]


@pytest.mark.db
async def test_there_is_no_heartbeat_before_the_worker_ever_ran(
    db_session: AsyncSession,
) -> None:
    """A fresh deployment: None rather than an error."""
    assert await last_heartbeat(db_session) is None


@pytest.mark.db
async def test_the_heartbeat_is_read_back_aware(db_session: AsyncSession) -> None:
    started = POSTED_AT
    beat = started + dt.timedelta(seconds=30)
    await record_heartbeat(db_session, beat_at=beat, session_started_at=started)

    heartbeat = await last_heartbeat(db_session)

    assert heartbeat is not None
    assert heartbeat.beat_at == beat
    assert heartbeat.session_started_at == started
