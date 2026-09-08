"""The boundary: discord.py objects in, `core.records` dataclasses out.

Nothing downstream of this module sees a `discord.*` type. That is what lets the
ingestion tests build records instead of faking a library we do not control, and it is
the reason the functions here stay as thin as they can be.

The two decisions that are *not* thin — how a reaction is keyed, and whether an update
event carries a content edit at all — are separate pure functions below, so they can be
tested with plain dicts and integers.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import discord

from core.records import (
    ChannelRecord,
    GuildRecord,
    MessageEvent,
    MessageRecord,
    ReactionRecord,
    UserRecord,
)


def emoji_key(name: str | None, emoji_id: int | None) -> str:
    """Return the stored form of a reaction emoji.

    A unicode emoji is its own key. A custom one is `name:id`, because two guilds can
    both have an `:aww:` and only the id tells them apart — while the name is what makes
    the row readable months later.

    Args:
        name: The emoji name, or the character itself for a unicode emoji.
        emoji_id: The snowflake of a custom emoji, None for a unicode one.

    Returns:
        The key to store.
    """
    if emoji_id is None:
        return name or ""
    return f"{name}:{emoji_id}"


def content_edit(data: Mapping[str, Any]) -> tuple[str, datetime] | None:
    """Extract a content edit from a raw update payload, if that is what it is.

    A message update event carries only the fields that changed, so most of them are
    not content edits at all: an embed resolved, a pin, a flag flipped. Writing an
    absent `content` as an edit would blank the message we already have — which is why
    this returns None rather than an empty string.

    Args:
        data: The raw `MESSAGE_UPDATE` payload. A Mapping and not a dict, because
            discord.py hands over a TypedDict, which no `dict[str, Any]` accepts.

    Returns:
        The new content and the edit timestamp, or None if the payload does not carry
        a content change.
    """
    if "content" not in data:
        return None

    edited_at = discord.utils.parse_time(data.get("edited_timestamp"))
    if edited_at is None:
        # An update without an edit timestamp is not a user edit (Discord sets it on
        # every real one), so there is nothing to stamp.
        return None
    return data["content"], edited_at


def message_event(message: discord.Message) -> MessageEvent | None:
    """Convert a gateway message and the dimensions it arrived with.

    Args:
        message: The message as discord.py built it.

    Returns:
        The event, or None for a direct message. DMs have no guild, the schema is keyed
        by one, and a private conversation is not what this project archives.
    """
    guild = message.guild
    if guild is None:
        return None

    channel_name = getattr(message.channel, "name", None)
    author = message.author
    reference = message.reference

    return MessageEvent(
        guild=GuildRecord(id=guild.id, name=guild.name),
        channel=ChannelRecord(
            id=message.channel.id, guild_id=guild.id, name=channel_name or "unknown"
        ),
        author=UserRecord(
            id=author.id,
            username=author.name,
            display_name=author.display_name,
            is_bot=author.bot,
        ),
        message=MessageRecord(
            id=message.id,
            channel_id=message.channel.id,
            author_id=author.id,
            created_at=message.created_at,
            content=message.content,
            reply_to_id=reference.message_id if reference else None,
            attachment_count=len(message.attachments),
            embed_count=len(message.embeds),
            edited_at=message.edited_at,
        ),
    )


def reaction_record(payload: discord.RawReactionActionEvent) -> ReactionRecord:
    """Convert a raw reaction event.

    Raw and not cached: `on_reaction_add` only fires for messages discord.py still holds
    in memory, which after a restart is none of the history.

    Args:
        payload: The raw reaction event.

    Returns:
        The reaction.
    """
    return ReactionRecord(
        message_id=payload.message_id,
        emoji=emoji_key(payload.emoji.name, payload.emoji.id),
        user_id=payload.user_id,
    )
