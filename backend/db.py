"""The API's database wiring: one engine for the process, one session per request.

`core` owns the schema; this module owns nothing but the lifetime. The engine is built
when the application starts and disposed when it stops, so a redeploy does not leak a
pool, and each request gets its own session from it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from litestar import Litestar
from litestar.datastructures import State
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import create_engine, create_session_factory

ENGINE_KEY = "db_engine"
SESSION_FACTORY_KEY = "db_sessions"


@asynccontextmanager
async def db_lifespan(app: Litestar) -> AsyncIterator[None]:
    """Hold one engine for the life of the application.

    Args:
        app: The application, whose state carries the engine and the session factory.

    Yields:
        Nothing; the engine is disposed on the way out.
    """
    engine = create_engine()
    app.state[ENGINE_KEY] = engine
    app.state[SESSION_FACTORY_KEY] = create_session_factory(engine)
    try:
        yield
    finally:
        # Disposed even when startup failed further along: a leaked pool holds
        # connections for the life of the container, and Postgres counts them.
        await engine.dispose()


async def provide_session(state: State) -> AsyncIterator[AsyncSession]:
    """Yield a session for one request.

    Args:
        state: The application state, holding the session factory.

    Yields:
        The session, closed when the request ends.
    """
    factory = state[SESSION_FACTORY_KEY]
    async with factory() as session:
        yield session
