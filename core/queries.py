"""Reads. Writes live in `core.upserts`.

Kept apart because the two have different rules: a write is an upsert and must be
idempotent, a read returns a flat record so that whatever decides on it stays a pure
function over values rather than over ORM instances.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import HEARTBEAT_ID, BotHeartbeat, Channel, IngestCursor, Message
from core.records import ChannelIngestRecord, CursorRecord, HeartbeatRecord


async def load_cursor(session: AsyncSession, channel_id: int) -> CursorRecord | None:
    """Return a channel's ingestion cursor.

    Args:
        session: The session to read through.
        channel_id: The channel to look up.

    Returns:
        The cursor, or None for a channel never ingested — which is the state a first
        backfill starts from, not an error.
    """
    row = await session.scalar(
        select(IngestCursor).where(IngestCursor.channel_id == channel_id)
    )
    if row is None:
        return None
    return CursorRecord(
        channel_id=row.channel_id,
        oldest_message_id=row.oldest_message_id,
        newest_message_id=row.newest_message_id,
        is_complete=row.is_complete,
    )


async def channel_ingest_status(session: AsyncSession) -> list[ChannelIngestRecord]:
    """Return, per channel, how far the ingestion has got and how much it holds.

    One query with two outer joins rather than one query per channel: the endpoint that
    reads this is the acceptance criterion of the whole component, and it should not
    cost fifty round trips to answer.

    A channel with no cursor is a channel never caught up, and it belongs in the answer
    precisely because that is what the reader wants to see.

    Args:
        session: The session to read through.

    Returns:
        One record per known channel, by channel id.
    """
    statement = (
        select(
            Channel.id,
            Channel.name,
            func.count(Message.id).label("message_count"),
            IngestCursor.oldest_message_id,
            IngestCursor.newest_message_id,
            IngestCursor.is_complete,
        )
        .outerjoin(IngestCursor, IngestCursor.channel_id == Channel.id)
        .outerjoin(Message, Message.channel_id == Channel.id)
        .group_by(
            Channel.id,
            Channel.name,
            IngestCursor.oldest_message_id,
            IngestCursor.newest_message_id,
            IngestCursor.is_complete,
        )
        .order_by(Channel.id)
    )
    rows = await session.execute(statement)
    return [
        ChannelIngestRecord(
            channel_id=row.id,
            channel_name=row.name,
            message_count=row.message_count,
            oldest_message_id=row.oldest_message_id,
            newest_message_id=row.newest_message_id,
            is_complete=bool(row.is_complete),
        )
        for row in rows
    ]


async def last_heartbeat(session: AsyncSession) -> HeartbeatRecord | None:
    """Return the worker's last heartbeat.

    Args:
        session: The session to read through.

    Returns:
        The heartbeat, or None when the worker has never run against this database.
    """
    row = await session.scalar(
        select(BotHeartbeat).where(BotHeartbeat.id == HEARTBEAT_ID)
    )
    if row is None:
        return None
    return HeartbeatRecord(
        beat_at=row.beat_at, session_started_at=row.session_started_at
    )
