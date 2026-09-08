import asyncio
import inspect
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.app import app
from backend.security import API_KEY_ENV_VAR
from core.config import DATABASE_URL_ENV_VAR, MisconfiguredError, harness_database_url
from core.db import create_engine

TEST_API_KEY = "test-api-key"
# Where the application points when no test database is configured. Never connected to:
# the engine is built at startup but only dials on the first query, so the routes that
# touch no database — and /api/health is one by design — work regardless.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"
PROJECT_ROOT = Path(__file__).parents[1]
REQUIRE_DB_OPTION = "--require-db"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the flag that turns a missing test database into a failure.

    `just test` stays usable with no database: the `db`-marked tests skip. `just check`
    passes this flag, because the gate before a push must not go green having run half
    the suite — a test skipped in silence is a test that does not exist.
    """
    parser.addoption(
        REQUIRE_DB_OPTION,
        action="store_true",
        default=False,
        help="Fail instead of skipping when the test database is unreachable.",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply the `anyio` marker to every async test.

    Avoids having to declare `pytestmark = pytest.mark.anyio` in every test module.
    """
    for item in items:
        if inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker("anyio")


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Configure anyio to use asyncio, else `pytestmark = pytest.mark.anyio` does not work."""
    return "asyncio"


@pytest.fixture(scope="session")
async def client() -> AsyncIterator[AsyncTestClient[Litestar]]:
    """Fixture for creating an async test client.

    The app refuses to start without an API key *or* a database URL, so both are set
    before the lifespan runs. The context manager restores the environment afterwards.

    The URL is the test database when there is one, so that a `db`-marked test can call
    a route that reads for real — and a placeholder otherwise. One client for the whole
    session either way: `app` is a module-level singleton, and a second lifespan over it
    would dispose the engine the first one is still holding.
    """
    try:
        database_url = harness_database_url()
    except MisconfiguredError:
        database_url = UNREACHABLE_DATABASE_URL

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv(API_KEY_ENV_VAR, TEST_API_KEY)
        monkeypatch.setenv(DATABASE_URL_ENV_VAR, database_url)
        app.debug = True
        async with AsyncTestClient(app=app) as _client:
            yield _client


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Configure a known API key for the duration of a test."""
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_API_KEY)
    yield TEST_API_KEY


def _no_database(request: pytest.FixtureRequest, reason: str) -> NoReturn:
    """Skip the requesting test, or fail it under `--require-db`.

    Args:
        request: The fixture request, read for the flag.
        reason: What was wrong, quoted verbatim into the message.

    Raises:
        Failed: Under `--require-db`.
        Skipped: Otherwise.
    """
    message = (
        f"{reason}\nStart a database with `just db`, or run `just test` to skip these."
    )
    if request.config.getoption(REQUIRE_DB_OPTION):
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


async def _probe(url: str) -> None:
    """Open and close one connection, to prove the database answers.

    Args:
        url: The connection URL to probe.
    """
    engine = create_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database(request: pytest.FixtureRequest) -> str:
    """Return the URL of a reachable, fully migrated test database.

    Synchronous on purpose. Alembic's env.py calls `asyncio.run`, which raises inside a
    running event loop, so the migration cannot happen in an async fixture — the
    friendliest-looking version of this fixture is the one that does not work.

    The URL is injected through `sqlalchemy.url` rather than the environment: alembic.ini
    leaves that option unset, so the same env.py serves production and this harness
    without either of them knowing about the other.

    Returns:
        The test database URL.
    """
    try:
        url = harness_database_url()
    except MisconfiguredError as exc:
        _no_database(request, str(exc))

    try:
        asyncio.run(_probe(url))
    except (SQLAlchemyError, OSError) as exc:
        _no_database(request, f"Test database unreachable: {exc}")

    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


@pytest.fixture(scope="session")
async def db_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    """Yield one engine for the whole session, disposed at the end.

    Args:
        migrated_database: URL of the migrated test database.

    Yields:
        The engine.
    """
    engine = create_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a session whose every write is rolled back when the test ends.

    The session is bound to an already-open connection holding a transaction, and
    `join_transaction_mode="create_savepoint"` makes the code under test commit into a
    savepoint instead of the real transaction. Without that argument a `commit()` in
    the code under test commits for good and the tests contaminate each other in an
    order that is painful to reproduce — which is why two tests in
    tests/test_db_harness.py exist solely to prove it.

    Note this factory is not `core.db.create_session_factory`: production binds the
    engine, the harness binds a connection. That is the whole difference.

    Args:
        db_engine: The session-scoped engine.

    Yields:
        A session on a doomed transaction.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session:
            yield session
        await transaction.rollback()
