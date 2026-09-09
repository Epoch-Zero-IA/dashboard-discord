"""Engine, session factory and declarative base for the shared schema."""

from typing import Any, cast

from sqlalchemy import CursorResult, MetaData, Result
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import database_url

# Every constraint and index gets a generated name rather than one the backend invents.
# Alembic autogenerate needs a deterministic name to emit a DROP against: an unnamed
# check or index is named by Postgres, differently from one database to the next, and
# the downgrade then fails on the one machine that matters.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for every table of the project.

    Plain SQLAlchemy rather than one of advanced-alchemy's bases: those supply a
    surrogate primary key (integer or UUID) and audit columns, and our primary keys are
    Discord snowflakes — given to us, 64-bit, and the natural identity of every row.
    advanced-alchemy still earns its place on the Litestar side, for session
    dependencies and repositories.
    """

    metadata = metadata


def create_engine(url: str | None = None) -> AsyncEngine:
    """Build the async engine.

    `pool_pre_ping` is on: after a database restart the pool holds sockets that are
    dead but look fine, and the next statement fails on a connection issue rather than
    on anything real. Pre-ping discards those quietly. It does not soften the fast-fail
    rule of the worker — a database that is actually down still raises, since there is
    nothing to reconnect to.

    Args:
        url: Connection URL. Defaults to the one in the environment.

    Returns:
        An engine, which the caller owns and must dispose of.
    """
    return create_async_engine(url or database_url(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory bound to `engine`.

    `expire_on_commit=False` because the worker reads attributes off an instance after
    committing it; the default would issue a fresh SELECT per attribute, one round trip
    per ingested message.

    Args:
        engine: The engine sessions will run on.

    Returns:
        A session factory.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


def rows_affected(result: Result[Any]) -> int:
    """Return how many rows a DML statement touched.

    `AsyncSession.execute` is typed as returning a `Result`, and the row count lives on
    the `CursorResult` an UPDATE or a DELETE actually produces. The cast is the
    documented way across that gap and costs nothing at runtime — and it is worth
    isolating, since several callers depend on the count to tell a real no-op from a
    write.

    Args:
        result: What the session returned.

    Returns:
        The number of rows affected.
    """
    return cast("CursorResult[Any]", result).rowcount
