"""Catches the gap between the models and the migrations, without a database.

Section 4 of the spec listed this as an accepted blind spot: the harness migrates a real
Postgres, so a model changed without a matching migration is only caught by tests that
need a database, and `just test` would pass. It turns out both sides can be rendered to
SQL offline — the models through a mock engine, the migrations through Alembic's `--sql`
mode — and compared. So the blind spot is closed for the cheap half of the problem: a
missing or divergent migration now fails the fast suite.

What this does *not* prove is that the SQL runs. Only a real Postgres does that, which
is still the job of the `db`-marked tests.
"""

import re
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_mock_engine

from core import models  # noqa: F401  (imported for its side effect on the metadata)
from core.db import metadata

PROJECT_ROOT = Path(__file__).parents[1]
# Never connected to: both renderings are offline. The dialect is what matters, since it
# decides how a partial index or a timestamptz comes out.
DIALECT_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:5432/unused"
# Alembic's own bookkeeping table is not part of the schema we design.
IGNORED_TABLE = "alembic_version"


def _statements(sql: str) -> set[str]:
    """Reduce a SQL script to its comparable CREATE statements.

    Whitespace is collapsed and statements are returned as a set: the two renderings
    emit the same objects in a different order, and ordering carries no meaning for a
    schema.

    Args:
        sql: A SQL script.

    Returns:
        One normalised string per CREATE TABLE or CREATE INDEX.
    """
    found: set[str] = set()
    for raw in sql.split(";"):
        # Comments go before the whitespace is collapsed, not after. Offline mode
        # prefixes the first statement with `-- Running upgrade`, and once the newlines
        # are gone there is no way to tell where the comment ended — a `--[^\n]*`
        # pattern then eats the statement it was meant to precede.
        body = " ".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        )
        collapsed = re.sub(r"\s+", " ", body).strip()
        if not re.match(r"CREATE (TABLE|INDEX)", collapsed, flags=re.IGNORECASE):
            continue
        if IGNORED_TABLE in collapsed:
            continue
        found.add(collapsed.replace(" ,", ","))
    return found


def _ddl_from_models() -> str:
    """Render the DDL the models describe.

    Returns:
        The full script.
    """
    buffer = StringIO()

    def write(sql, *args, **kwargs) -> None:
        buffer.write(f"{sql.compile(dialect=engine.dialect)};")

    engine = create_mock_engine(DIALECT_URL, write)
    metadata.create_all(engine, checkfirst=False)
    return buffer.getvalue()


def _ddl_from_migrations() -> str:
    """Render the DDL the migration history produces, in Alembic's offline mode.

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


def test_the_migrations_build_exactly_what_the_models_describe() -> None:
    """A model without its migration, or a migration that drifted, fails here."""
    from_models = _statements(_ddl_from_models())
    from_migrations = _statements(_ddl_from_migrations())

    assert from_models, "rendered nothing from the models — the metadata is empty"
    missing = from_models - from_migrations
    extra = from_migrations - from_models
    assert not missing, f"no migration builds: {sorted(missing)}"
    assert not extra, (
        f"the migrations build something the models do not describe: {sorted(extra)}"
    )
