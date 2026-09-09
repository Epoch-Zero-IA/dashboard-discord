"""Deux garde-fous contre la dérive entre les modèles et les migrations.

Le premier est **hors base** : il vérifie que chaque table décrite par les modèles est
bien créée par une migration. C'est le cas de dérive le plus courant — un modèle ajouté
sans sa révision — et il est attrapé par `just test`, sans Docker.

Le second est **exact, et exige une base**. Il applique l'historique complet puis demande
à l'autogenerate d'Alembic ce qu'il resterait à faire : la réponse doit être « rien ».
C'est le seul niveau qui voit une colonne, un type ou une contrainte manquants.

La première version de ce fichier comparait le DDL rendu des deux côtés, en croyant
couvrir le second cas hors base. Ça n'a tenu que le temps de la première migration
faisant un `ALTER TABLE` : les modèles rendent une seule `CREATE TABLE` complète, la
migration une `CREATE TABLE` d'origine suivie d'un `ALTER`, et les deux textes ne peuvent
pas coïncider. Comparer du DDL rendu ne dit rien de l'état final d'un schéma.
"""

import re
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, create_mock_engine
from sqlalchemy.ext.asyncio import AsyncEngine

from core import models  # noqa: F401  (imported for its side effect on the metadata)
from core.db import metadata

PROJECT_ROOT = Path(__file__).parents[1]
# Never connected to: the offline rendering only needs a dialect, and the dialect is what
# decides how a partial index or a timestamptz comes out.
DIALECT_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:5432/unused"
# Alembic's own bookkeeping table is not part of the schema we design.
IGNORED_TABLE = "alembic_version"


def _created_tables(sql: str) -> set[str]:
    """Return the names of the tables a SQL script creates.

    Args:
        sql: A SQL script.

    Returns:
        One name per CREATE TABLE.
    """
    found = set()
    for statement in sql.split(";"):
        # Comments go before the whitespace is collapsed: offline mode prefixes the
        # first statement with `-- Running upgrade`, and once the newlines are gone a
        # `--[^\n]*` pattern would eat the statement it was meant to precede.
        body = " ".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        )
        match = re.match(r"\s*CREATE TABLE (\w+)", body, flags=re.IGNORECASE)
        if match and match.group(1) != IGNORED_TABLE:
            found.add(match.group(1))
    return found


def _ddl_from_migrations() -> str:
    """Render the whole migration history in Alembic's offline mode.

    The URL is injected rather than read from the environment, so the test needs no
    DATABASE_URL and cannot accidentally reach a real database.

    Returns:
        The full script.
    """
    buffer = StringIO()
    # `output_buffer`, not `stdout`: offline mode writes the script through
    # `config.output_buffer`, and a Config given only a stdout captures nothing.
    config = Config(PROJECT_ROOT / "alembic.ini", output_buffer=buffer, stdout=buffer)
    config.set_main_option("sqlalchemy.url", DIALECT_URL)
    command.upgrade(config, "head", sql=True)
    return buffer.getvalue()


def test_every_table_of_the_models_is_created_by_a_migration() -> None:
    """A model added without its revision fails here, with no database in sight."""
    buffer = StringIO()

    def write(sql, *args, **kwargs) -> None:
        buffer.write(f"{sql.compile(dialect=engine.dialect)};")

    engine = create_mock_engine(DIALECT_URL, write)
    metadata.create_all(engine, checkfirst=False)

    from_models = _created_tables(buffer.getvalue())
    from_migrations = _created_tables(_ddl_from_migrations())

    assert from_models, "rendered nothing from the models — the metadata is empty"
    assert not from_models - from_migrations, (
        f"no migration creates: {sorted(from_models - from_migrations)}"
    )
    assert not from_migrations - from_models, (
        f"the migrations create tables no model describes: "
        f"{sorted(from_migrations - from_models)}"
    )


@pytest.mark.db
async def test_the_migrated_schema_matches_the_models_exactly(
    db_engine: AsyncEngine,
) -> None:
    """After `upgrade head`, autogenerate must find nothing left to do.

    This is the rigorous half: it compares columns, types and constraints against a
    schema that was actually built by the migrations, which is the only way to catch a
    forgotten `ALTER`. The `migrated_database` fixture has already applied the history.
    """

    def diff(connection: Connection) -> list[tuple]:
        context = MigrationContext.configure(connection)
        return compare_metadata(context, metadata)

    async with db_engine.connect() as connection:
        differences = await connection.run_sync(diff)

    assert differences == [], f"le schéma migré diverge des modèles : {differences}"
