"""Flat records: the contract between whoever observes Discord and the schema.

These are the argument types of `core.upserts`, which is why they live here and not in
`bot/`: `core` may not import `bot`, and duplicating the same eight fields on both
sides of that line is how the two drift apart.

They are also what makes the worker testable. `bot/adapters.py` turns a discord.py
object into one of these at the boundary and nothing downstream ever sees a
`discord.Message`, so the ingestion tests build records instead of faking a library we
do not control.

Frozen and slotted: a record is a description of something that already happened, and
nothing downstream has any business editing one.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class GuildRecord:
    """A guild as last seen."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class ChannelRecord:
    """A channel as last seen."""

    id: int
    guild_id: int
    name: str


@dataclass(frozen=True, slots=True)
class UserRecord:
    """An account as last seen."""

    id: int
    username: str
    display_name: str | None = None
    is_bot: bool = False


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """A message as posted, or as found again during a catch-up."""

    id: int
    channel_id: int
    author_id: int
    created_at: datetime
    content: str | None = None
    reply_to_id: int | None = None
    attachment_count: int = 0
    embed_count: int = 0
    edited_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReactionRecord:
    """One reaction, by one account, on one message."""

    message_id: int
    emoji: str
    user_id: int


@dataclass(frozen=True, slots=True)
class MessageEvent:
    """A message together with the dimensions it arrived with.

    The gateway hands over the guild, the channel and the author on every event, so the
    dimensions are refreshed for free — no extra API call to keep a name current.
    """

    guild: GuildRecord
    channel: ChannelRecord
    author: UserRecord
    message: MessageRecord
