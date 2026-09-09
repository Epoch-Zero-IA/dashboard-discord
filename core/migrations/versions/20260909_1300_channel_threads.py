"""Les fils sont des canaux : channel.parent_id et channel.is_thread

Revision ID: 20260909_1300
Revises: 20260908_1800
Create Date: 2026-09-09

Un fil Discord porte des messages exactement comme un canal, et la première connexion à
un vrai serveur l'a montré : sur 132 canaux, une partie des conversations vit dans des
fils. Le socle les ingérait en temps réel — `on_message` ne fait pas la différence — mais
ne les rattrapait jamais, et rien en base ne distinguait un fil d'un canal.

`is_thread` porte un défaut serveur parce que la colonne est NOT NULL et que la table
peut déjà contenir des lignes : sans lui, l'ALTER échoue sur une base non vide.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_1300"
down_revision: str | None = "20260908_1800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No foreign key on parent_id, on purpose: a thread can be readable while its parent
    # channel is not, and a constraint would reject the thread instead of the one thing
    # we cannot fix.
    op.add_column("channel", sa.Column("parent_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "channel",
        sa.Column(
            "is_thread",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("channel", "is_thread")
    op.drop_column("channel", "parent_id")
