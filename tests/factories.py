"""Builders for the flat records, shared by the tests that need one.

Not a fixture module: these are plain functions, so a test can build three messages of
its own without asking the fixture machinery for anything.
"""

from datetime import UTC, datetime

from core.records import (
    ChannelRecord,
    GuildRecord,
    MessageEvent,
    MessageRecord,
    UserRecord,
)

GUILD_ID = 1000000000000000001
CHANNEL_ID = 1000000000000000002
AUTHOR_ID = 1000000000000000003
MESSAGE_ID = 1000000000000000004
POSTED_AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def an_event(
    *,
    content: str | None = "bonjour",
    message_id: int = MESSAGE_ID,
    channel_id: int = CHANNEL_ID,
    created_at: datetime = POSTED_AT,
    parent_id: int | None = None,
    is_thread: bool = False,
) -> MessageEvent:
    """Build a message event with the dimensions it would have arrived with.

    Args:
        content: The message body.
        message_id: The snowflake to use.
        channel_id: The channel it was posted in.
        created_at: When it was posted. The aggregation tests place messages on
            several different days.
        parent_id: For a thread, the channel it hangs under.
        is_thread: Whether the message was posted in a thread rather than a channel.

    Returns:
        The event, ready to ingest.
    """
    return MessageEvent(
        guild=GuildRecord(id=GUILD_ID, name="Serveur de test"),
        channel=ChannelRecord(
            id=channel_id,
            guild_id=GUILD_ID,
            name="general",
            parent_id=parent_id,
            is_thread=is_thread,
        ),
        author=UserRecord(id=AUTHOR_ID, username="alice", display_name="Alice"),
        message=MessageRecord(
            id=message_id,
            channel_id=channel_id,
            author_id=AUTHOR_ID,
            created_at=created_at,
            content=content,
        ),
    )
