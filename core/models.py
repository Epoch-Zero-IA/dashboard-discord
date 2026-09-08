"""The tables. Discord snowflakes are the primary keys, so every write is an upsert.

Two properties hold everywhere and the rest of the project leans on them:

- **Given identity.** A snowflake is a 64-bit id Discord assigns, not something we
  generate, so there is no surrogate key anywhere and re-ingesting the same event
  cannot create a second row. `autoincrement=False` says so to the DDL: without it
  SQLAlchemy would hand a BIGINT primary key an identity sequence.
- **Aware timestamps.** Every datetime is `timestamptz`. Discord gives UTC, and a naive
  column would silently drop the offset — the kind of bug that only shows up when the
  retention purge compares two datetimes of different kinds.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base

# Discord caps guild and channel names at 100 characters, usernames at 32. Sized
# columns rather than Text: a value longer than that means we misread the payload, and
# failing on it is better than storing it.
NAME_LENGTH = 100
USERNAME_LENGTH = 32
# Unicode emoji, or `name:id` for a custom one. Names cap at 32, ids at 20.
EMOJI_LENGTH = 64


class Guild(Base):
    """A Discord server. Refreshed whenever we come across it."""

    __tablename__ = "guild"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Channel(Base):
    """A text channel of a guild."""

    __tablename__ = "channel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guild.id"))
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DiscordUser(Base):
    """A Discord account, member or bot.

    Named `discord_user` rather than `user`: `user` is a reserved word in Postgres, and
    every hand-written query would need it quoted.
    """

    __tablename__ = "discord_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(USERNAME_LENGTH))
    display_name: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    """A message, kept until the retention purge takes its content away.

    `content` is nullable and `deleted_at` is the flag: a deletion empties the content
    and keeps the row, so the member's deletion is honoured while the statistics stay
    right. The row surviving is what makes `daily_activity` recomputable.
    """

    __tablename__ = "message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channel.id"))
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("discord_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[str | None] = mapped_column(Text)
    # Deliberately *not* a foreign key. A reply can point at a message we do not have:
    # older than the backfill has reached, or already purged. A constraint here would
    # reject the reply instead of the one thing we cannot fix — the absent target.
    reply_to_id: Mapped[int | None] = mapped_column(BigInteger)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    embed_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        # The dashboard reads per channel over a window, the nightly aggregation reads
        # per author over a day.
        Index("ix_message_channel_id_created_at", "channel_id", "created_at"),
        Index("ix_message_author_id_created_at", "author_id", "created_at"),
        # Partial: the purge only ever looks for rows that still hold content, and they
        # are a shrinking minority of the table after ninety days.
        Index(
            "ix_message_created_at_live",
            "created_at",
            postgresql_where=(deleted_at.is_(None)),
        ),
    )


class Reaction(Base):
    """One reaction by one user on one message.

    Cascades on the message: the nightly purge deletes message rows, and a reaction
    whose message is gone counts nothing.
    """

    __tablename__ = "reaction"

    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("message.id", ondelete="CASCADE"), primary_key=True
    )
    emoji: Mapped[str] = mapped_column(String(EMOJI_LENGTH), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discord_user.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyActivity(Base):
    """Messages and characters per day, per channel, per author.

    Outlives the purge — indefinitely — which is how the dashboard keeps its history
    without keeping the conversations. Recomputable from `message` inside the retention
    window, so the aggregation can be replayed at will.

    The date column is `activity_date`, not `date`: a column named after a type reads
    badly in every query that joins on it.
    """

    __tablename__ = "daily_activity"

    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channel.id"), primary_key=True
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discord_user.id"), primary_key=True
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    character_count: Mapped[int] = mapped_column(Integer, default=0)


class IngestCursor(Base):
    """How far the ingestion has got in one channel, in both directions.

    One row per channel, and the single source of truth for the catch-up machinery:
    `oldest_message_id` walks backwards during the initial backfill,
    `newest_message_id` forwards when filling a gateway gap. `is_complete` says the
    backfill has reached the beginning of the channel and never has to go back.
    """

    __tablename__ = "ingest_cursor"

    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channel.id"), primary_key=True, autoincrement=False
    )
    oldest_message_id: Mapped[int | None] = mapped_column(BigInteger)
    newest_message_id: Mapped[int | None] = mapped_column(BigInteger)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BotHeartbeat(Base):
    """The worker's proof of life, and what its healthcheck reads.

    A single row, pinned by a check constraint. The heartbeat is in the database rather
    than behind an HTTP port because it has to prove three things at once: the process
    lives, the gateway connection is up, and the database is reachable. A `/health`
    endpoint proves only the first.
    """

    __tablename__ = "bot_heartbeat"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The gateway session this beat belongs to: a beat_at that stops moving means a
    # hung worker, a session_started_at that keeps moving means a restart loop.
    session_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


# The one row of bot_heartbeat.
HEARTBEAT_ID = 1
