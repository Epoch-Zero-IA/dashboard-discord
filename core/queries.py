"""Reads. Writes live in `core.upserts`.

Kept apart because the two have different rules: a write is an upsert and must be
idempotent, a read returns a flat record so that whatever decides on it stays a pure
function over values rather than over ORM instances.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import IngestCursor
from core.records import CursorRecord


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
