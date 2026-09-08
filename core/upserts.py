"""Every write to the schema, and the only place `ON CONFLICT` is spelled out.

Two rules the rest of the project relies on:

- **Idempotence.** Snowflakes are the primary keys, so every insert is an upsert and
  ingesting the same event twice is indistinguishable from ingesting it once. The
  catch-up machinery replays overlapping pages on purpose; this is what makes that
  free.
- **Orphans are a no-op, not an error.** An edit or a delete can name a message we do
  not have: the backfill has not reached it, or the purge already took it. Those
  functions return whether they touched anything, and the caller logs rather than
  raises. After ninety days of life this happens daily.

`updated_at` is set explicitly in every `set_` clause. SQLAlchemy's `onupdate` hook
does not fire for `ON CONFLICT DO UPDATE` — the update happens inside the database,
where no Python default can reach it — so a column left out of `set_` would keep the
timestamp of the first insert forever.
"""

from datetime import datetime

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import rows_affected
from core.models import (
    HEARTBEAT_ID,
    BotHeartbeat,
    Channel,
    DiscordUser,
    Guild,
    IngestCursor,
    Message,
    Reaction,
)
from core.records import (
    ChannelRecord,
    GuildRecord,
    MessageEvent,
    MessageRecord,
    ReactionRecord,
    UserRecord,
)


async def upsert_guild(session: AsyncSession, guild: GuildRecord) -> None:
    """Insert the guild, or refresh its name.

    Args:
        session: The session to write through.
        guild: The guild as last seen.
    """
    statement = insert(Guild).values(id=guild.id, name=guild.name)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Guild.id],
            set_={"name": statement.excluded.name, "updated_at": func.now()},
        )
    )


async def upsert_channel(session: AsyncSession, channel: ChannelRecord) -> None:
    """Insert the channel, or refresh its name.

    Args:
        session: The session to write through.
        channel: The channel as last seen.
    """
    statement = insert(Channel).values(
        id=channel.id, guild_id=channel.guild_id, name=channel.name
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Channel.id],
            set_={"name": statement.excluded.name, "updated_at": func.now()},
        )
    )


async def upsert_user(session: AsyncSession, user: UserRecord) -> None:
    """Insert the account, or refresh its names.

    Args:
        session: The session to write through.
        user: The account as last seen.
    """
    statement = insert(DiscordUser).values(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_bot=user.is_bot,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[DiscordUser.id],
            set_={
                "username": statement.excluded.username,
                "display_name": statement.excluded.display_name,
                "is_bot": statement.excluded.is_bot,
                "updated_at": func.now(),
            },
        )
    )


async def upsert_message(session: AsyncSession, message: MessageRecord) -> None:
    """Insert the message, or overwrite its mutable fields.

    `deleted_at` is never touched here. A catch-up that re-reads a message we have
    already seen deleted must not resurrect its content: the deletion is the more
    recent truth, whatever order the two events reach us in.

    Args:
        session: The session to write through.
        message: The message as posted or as found again.
    """
    statement = insert(Message).values(
        id=message.id,
        channel_id=message.channel_id,
        author_id=message.author_id,
        created_at=message.created_at,
        edited_at=message.edited_at,
        content=message.content,
        reply_to_id=message.reply_to_id,
        attachment_count=message.attachment_count,
        embed_count=message.embed_count,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Message.id],
            set_={
                "content": statement.excluded.content,
                "edited_at": statement.excluded.edited_at,
                "attachment_count": statement.excluded.attachment_count,
                "embed_count": statement.excluded.embed_count,
            },
            # Only when the row still holds content. Without this the clause above
            # would undo a deletion.
            where=Message.deleted_at.is_(None),
        )
    )


async def ingest_message(session: AsyncSession, event: MessageEvent) -> None:
    """Write a message and the dimensions it arrived with, in the order the FKs need.

    The ordering is a property of the schema, not of the worker, which is why it lives
    here: `message` references `channel` and `discord_user`, so a message inserted
    first would be rejected on its very first appearance in a new channel.

    Args:
        session: The session to write through.
        event: The message and its dimensions.
    """
    await upsert_guild(session, event.guild)
    await upsert_channel(session, event.channel)
    await upsert_user(session, event.author)
    await upsert_message(session, event.message)


async def apply_message_edit(
    session: AsyncSession, message_id: int, content: str | None, edited_at: datetime
) -> bool:
    """Overwrite the content of a message and stamp the edit.

    No version history: the spec keeps the current text only.

    Args:
        session: The session to write through.
        message_id: The message that was edited.
        content: Its new content.
        edited_at: When Discord says the edit happened.

    Returns:
        False when no such live message exists — an orphan edit, which is a no-op.
    """
    result = await session.execute(
        update(Message)
        .where(Message.id == message_id, Message.deleted_at.is_(None))
        .values(content=content, edited_at=edited_at)
    )
    return rows_affected(result) > 0


async def mark_message_deleted(
    session: AsyncSession, message_id: int, deleted_at: datetime
) -> bool:
    """Flag a message as deleted and drop its content, keeping the row.

    The member's deletion is honoured — the text is gone — while the row keeps the
    statistics right and `daily_activity` recomputable.

    Args:
        session: The session to write through.
        message_id: The message that was deleted.
        deleted_at: When it was deleted.

    Returns:
        False when no such message exists, which is a no-op rather than an error.
    """
    result = await session.execute(
        update(Message)
        .where(Message.id == message_id, Message.deleted_at.is_(None))
        .values(content=None, deleted_at=deleted_at)
    )
    return rows_affected(result) > 0


async def upsert_reaction(session: AsyncSession, reaction: ReactionRecord) -> None:
    """Record a reaction, ignoring one we already have.

    `DO NOTHING` and not `DO UPDATE`: the primary key is the whole row, so a conflict
    means we are being told something we already know.

    Args:
        session: The session to write through.
        reaction: The reaction as observed.
    """
    statement = insert(Reaction).values(
        message_id=reaction.message_id, emoji=reaction.emoji, user_id=reaction.user_id
    )
    await session.execute(statement.on_conflict_do_nothing())


async def remove_reaction(session: AsyncSession, reaction: ReactionRecord) -> bool:
    """Delete a reaction that was taken back.

    Args:
        session: The session to write through.
        reaction: The reaction that was removed.

    Returns:
        False when we never had it, which is a no-op.
    """
    result = await session.execute(
        delete(Reaction).where(
            Reaction.message_id == reaction.message_id,
            Reaction.emoji == reaction.emoji,
            Reaction.user_id == reaction.user_id,
        )
    )
    return rows_affected(result) > 0


async def record_heartbeat(
    session: AsyncSession, beat_at: datetime, session_started_at: datetime
) -> None:
    """Move the worker's proof of life forward.

    Args:
        session: The session to write through.
        beat_at: Now, as the worker sees it.
        session_started_at: When the current gateway session was established.
    """
    statement = insert(BotHeartbeat).values(
        id=HEARTBEAT_ID, beat_at=beat_at, session_started_at=session_started_at
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[BotHeartbeat.id],
            set_={
                "beat_at": statement.excluded.beat_at,
                "session_started_at": statement.excluded.session_started_at,
            },
        )
    )


async def save_cursor(
    session: AsyncSession,
    channel_id: int,
    *,
    oldest_message_id: int | None = None,
    newest_message_id: int | None = None,
    is_complete: bool | None = None,
) -> None:
    """Write a channel's ingestion cursor, leaving the fields not given alone.

    Written after every page of a catch-up, which is what bounds the loss from an
    interruption to a single page. The partial update matters: a backfill walking
    backwards moves `oldest_message_id` only, and must not blank the other end.

    Args:
        session: The session to write through.
        channel_id: The channel being caught up.
        oldest_message_id: New value for the backward cursor, if it moved.
        newest_message_id: New value for the forward cursor, if it moved.
        is_complete: Whether the backfill has reached the start of the channel.
    """
    values: dict[str, int | bool | None] = {"channel_id": channel_id}
    if oldest_message_id is not None:
        values["oldest_message_id"] = oldest_message_id
    if newest_message_id is not None:
        values["newest_message_id"] = newest_message_id
    if is_complete is not None:
        values["is_complete"] = is_complete

    statement = insert(IngestCursor).values(**values)
    updatable = {key: statement.excluded[key] for key in values if key != "channel_id"}
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[IngestCursor.channel_id],
            set_={**updatable, "updated_at": func.now()},
        )
    )
