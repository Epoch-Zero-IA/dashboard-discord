import inspect
from collections.abc import AsyncIterator, Iterator

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from backend.app import app
from backend.security import API_KEY_ENV_VAR

TEST_API_KEY = "test-api-key"


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

    The app refuses to start without an API key, so one is set before the lifespan
    runs. The context manager restores the environment afterwards.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv(API_KEY_ENV_VAR, TEST_API_KEY)
        app.debug = True
        async with AsyncTestClient(app=app) as _client:
            yield _client


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Configure a known API key for the duration of a test."""
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_API_KEY)
    yield TEST_API_KEY
