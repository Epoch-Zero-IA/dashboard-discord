"""The write path, on a real Postgres.

Every test here is `db`-marked: these are the properties SQLite could not have proved
(ON CONFLICT on a composite key, tz-aware comparisons, a partial index), which is why
section 4 of the spec puts them on a real engine.

The whole file rests on one invariant from section 2: Discord snowflakes are the primary
keys, so every write is an upsert and replaying an event is indistinguishable from
seeing it once. The catch-up machinery of step 4 replays overlapping pages on purpose.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    HEARTBEAT_ID,
    BotHeartbeat,
    Channel,
    IngestCursor,
    Message,
    Reaction,
)
from core.records import ReactionRecord
from core.upserts import (
    apply_message_edit,
    ingest_message,
    mark_message_deleted,
    record_heartbeat,
    remove_reaction,
    save_cursor,
    upsert_reaction,
)
from tests.factories import AUTHOR_ID, CHANNEL_ID, MESSAGE_ID, POSTED_AT, an_event


@pytest.mark.db
async def test_ingesting_the_same_message_twice_leaves_one_row(
    db_session: AsyncSession,
) -> None:
    """Idempotence, the property everything else is built on."""
    await ingest_message(db_session, an_event())
    await ingest_message(db_session, an_event())

    count = await db_session.scalar(select(func.count()).select_from(Message))
    assert count == 1


@pytest.mark.db
async def test_created_at_comes_back_aware_and_unchanged(
    db_session: AsyncSession,
) -> None:
    """timestamptz round-trips with its offset.

    The check SQLite could not have made: it has no aware type, would have dropped the
    offset on the way in and returned a naive datetime, and every later comparison
    would have raised or silently shifted.
    """
    await ingest_message(db_session, an_event())

    stored = await db_session.scalar(select(Message.created_at))
    assert stored is not None
    assert stored.tzinfo is not None
    assert stored == POSTED_AT


@pytest.mark.db
async def test_deleting_empties_the_content_and_keeps_the_row(
    db_session: AsyncSession,
) -> None:
    """The member's deletion is honoured; the statistics stay right."""
    await ingest_message(db_session, an_event())
    deleted_at = POSTED_AT + timedelta(minutes=5)

    assert await mark_message_deleted(db_session, MESSAGE_ID, deleted_at) is True

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.content is None
    assert row.deleted_at == deleted_at


@pytest.mark.db
async def test_a_catch_up_does_not_resurrect_a_deleted_message(
    db_session: AsyncSession,
) -> None:
    """Re-reading a deleted message during a catch-up must not bring its text back.

    The deletion is the more recent truth whichever order the two events reach us in,
    which is why the upsert carries `WHERE deleted_at IS NULL`.
    """
    await ingest_message(db_session, an_event())
    await mark_message_deleted(db_session, MESSAGE_ID, POSTED_AT + timedelta(minutes=5))

    await ingest_message(db_session, an_event(content="bonjour"))

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.content is None


@pytest.mark.db
async def test_an_edit_for_an_unknown_message_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    """An orphan edit returns False instead of raising.

    It happens when the backfill has not reached the message yet, and every day once
    the purge starts biting — so it is a normal outcome, not an error.
    """
    edited = await apply_message_edit(
        db_session, MESSAGE_ID, "trop tard", POSTED_AT + timedelta(hours=1)
    )
    assert edited is False


@pytest.mark.db
async def test_an_edit_on_a_deleted_message_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    """Same rule as the catch-up: nothing writes content back into a deleted row."""
    await ingest_message(db_session, an_event())
    await mark_message_deleted(db_session, MESSAGE_ID, POSTED_AT + timedelta(minutes=1))

    edited = await apply_message_edit(
        db_session, MESSAGE_ID, "revenu", POSTED_AT + timedelta(minutes=2)
    )
    assert edited is False


@pytest.mark.db
async def test_a_reaction_is_keyed_by_the_whole_triple(
    db_session: AsyncSession,
) -> None:
    """The composite primary key is the ON CONFLICT target SQLite could not test."""
    await ingest_message(db_session, an_event())
    reaction = ReactionRecord(message_id=MESSAGE_ID, emoji="👍", user_id=AUTHOR_ID)

    await upsert_reaction(db_session, reaction)
    await upsert_reaction(db_session, reaction)
    await upsert_reaction(
        db_session, ReactionRecord(message_id=MESSAGE_ID, emoji="🎉", user_id=AUTHOR_ID)
    )

    count = await db_session.scalar(select(func.count()).select_from(Reaction))
    assert count == 2


@pytest.mark.db
async def test_removing_a_reaction_we_never_had_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    removed = await remove_reaction(
        db_session,
        ReactionRecord(message_id=MESSAGE_ID, emoji="👍", user_id=AUTHOR_ID),
    )
    assert removed is False


@pytest.mark.db
async def test_purging_a_message_takes_its_reactions_with_it(
    db_session: AsyncSession,
) -> None:
    """ON DELETE CASCADE, which the nightly purge depends on.

    The purge deletes message rows; a reaction whose message is gone would otherwise
    hold a foreign key to nothing and block the delete.
    """
    await ingest_message(db_session, an_event())
    await upsert_reaction(
        db_session, ReactionRecord(message_id=MESSAGE_ID, emoji="👍", user_id=AUTHOR_ID)
    )
    await db_session.flush()

    message = (await db_session.execute(select(Message))).scalar_one()
    await db_session.delete(message)
    await db_session.flush()

    count = await db_session.scalar(select(func.count()).select_from(Reaction))
    assert count == 0


@pytest.mark.db
async def test_saving_one_end_of_a_cursor_leaves_the_other_alone(
    db_session: AsyncSession,
) -> None:
    """The partial update the catch-up relies on.

    A backfill walks backwards and moves `oldest_message_id` only. Were the other end
    blanked, the next gateway-gap catch-up would have no idea where it had got to.
    """
    await ingest_message(db_session, an_event())
    await save_cursor(db_session, CHANNEL_ID, newest_message_id=MESSAGE_ID)

    await save_cursor(db_session, CHANNEL_ID, oldest_message_id=MESSAGE_ID - 100)

    cursor = (await db_session.execute(select(IngestCursor))).scalar_one()
    assert cursor.newest_message_id == MESSAGE_ID
    assert cursor.oldest_message_id == MESSAGE_ID - 100
    assert cursor.is_complete is False


@pytest.mark.db
async def test_the_heartbeat_stays_a_single_row(db_session: AsyncSession) -> None:
    """Beating twice moves the timestamp instead of adding a row.

    The check constraint pins the id, so a second insert has to be an upsert — this is
    what the compose healthcheck reads.
    """
    started = POSTED_AT
    await record_heartbeat(db_session, beat_at=started, session_started_at=started)
    later = started + timedelta(seconds=30)
    await record_heartbeat(db_session, beat_at=later, session_started_at=started)

    rows = (await db_session.execute(select(BotHeartbeat))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == HEARTBEAT_ID
    assert rows[0].beat_at == later
    assert rows[0].session_started_at == started


@pytest.mark.db
async def test_a_thread_is_recorded_as_a_channel_that_knows_its_parent(
    db_session: AsyncSession,
) -> None:
    """Threads share the `channel` table, flagged and pointing at their parent.

    Discovered on the first real connection: a good part of the conversation lives in
    threads, `on_message` makes no distinction, and the catch-up used to skip them —
    so a thread was ingested live and never backfilled.
    """
    await ingest_message(
        db_session,
        an_event(message_id=42, channel_id=999, parent_id=CHANNEL_ID, is_thread=True),
    )

    channel = (
        await db_session.execute(select(Channel).where(Channel.id == 999))
    ).scalar_one()
    assert channel.is_thread is True
    assert channel.parent_id == CHANNEL_ID


@pytest.mark.db
async def test_an_ordinary_channel_is_not_flagged_as_a_thread(
    db_session: AsyncSession,
) -> None:
    """The default matters: the column is NOT NULL and every existing row predates it."""
    await ingest_message(db_session, an_event())

    channel = (
        await db_session.execute(select(Channel).where(Channel.id == CHANNEL_ID))
    ).scalar_one()
    assert channel.is_thread is False
    assert channel.parent_id is None
