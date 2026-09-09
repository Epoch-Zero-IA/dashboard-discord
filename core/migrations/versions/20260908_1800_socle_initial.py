"""Socle initial : dimensions, messages, réactions, agrégat, curseurs, heartbeat

Revision ID: 20260908_1800
Revises:
Create Date: 2026-09-08

The naming convention of `core.db.metadata` is in force here too: env.py passes that
metadata as `target_metadata`, and Alembic applies its convention to everything
`op.*` creates. So constraint names are given in the same form the models give them —
the bare token for a check constraint, since `ck_%(table_name)s_%(constraint_name)s`
prefixes it. Spelling out `ck_bot_heartbeat_single_row` here produced
`ck_bot_heartbeat_ck_bot_heartbeat_single_row`, caught by diffing this migration's
rendered SQL against the DDL of the models. The primary and foreign key names are
redundant for the same reason — their conventions ignore the name given — but they are
kept because that is what autogenerate emits, and a reader should not have to know
which of the two wins.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_1800"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guild"),
    )
    op.create_table(
        "discord_user",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_discord_user"),
    )
    op.create_table(
        "bot_heartbeat",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
        sa.PrimaryKeyConstraint("id", name="pk_bot_heartbeat"),
    )
    op.create_table(
        "channel",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["guild_id"], ["guild.id"], name="fk_channel_guild_id_guild"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channel"),
    )
    op.create_table(
        "message",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("reply_to_id", sa.BigInteger(), nullable=True),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.Column("embed_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channel.id"], name="fk_message_channel_id_channel"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["discord_user.id"],
            name="fk_message_author_id_discord_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_message"),
    )
    op.create_index(
        "ix_message_author_id_created_at", "message", ["author_id", "created_at"]
    )
    op.create_index(
        "ix_message_channel_id_created_at", "message", ["channel_id", "created_at"]
    )
    op.create_index(
        "ix_message_created_at_live",
        "message",
        ["created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "daily_activity",
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channel.id"], name="fk_daily_activity_channel_id_channel"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["discord_user.id"],
            name="fk_daily_activity_author_id_discord_user",
        ),
        sa.PrimaryKeyConstraint(
            "activity_date", "channel_id", "author_id", name="pk_daily_activity"
        ),
    )
    op.create_table(
        "ingest_cursor",
        sa.Column("channel_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("oldest_message_id", sa.BigInteger(), nullable=True),
        sa.Column("newest_message_id", sa.BigInteger(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channel.id"], name="fk_ingest_cursor_channel_id_channel"
        ),
        sa.PrimaryKeyConstraint("channel_id", name="pk_ingest_cursor"),
    )
    op.create_table(
        "reaction",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["message.id"],
            name="fk_reaction_message_id_message",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["discord_user.id"], name="fk_reaction_user_id_discord_user"
        ),
        sa.PrimaryKeyConstraint("message_id", "emoji", "user_id", name="pk_reaction"),
    )


def downgrade() -> None:
    op.drop_table("reaction")
    op.drop_table("ingest_cursor")
    op.drop_table("daily_activity")
    op.drop_index("ix_message_created_at_live", table_name="message")
    op.drop_index("ix_message_channel_id_created_at", table_name="message")
    op.drop_index("ix_message_author_id_created_at", table_name="message")
    op.drop_table("message")
    op.drop_table("channel")
    op.drop_table("bot_heartbeat")
    op.drop_table("discord_user")
    op.drop_table("guild")
