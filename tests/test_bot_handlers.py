"""Handler isolation: what gets swallowed, and what must not be."""

import pytest
from sqlalchemy.exc import OperationalError

from bot.handlers import isolate


async def test_an_ordinary_failure_does_not_escape_the_handler() -> None:
    """One malformed event must not cost the connection.

    A worker that dies on the first surprising message stops ingesting the forty-nine
    other channels too.
    """
    calls: list[str] = []

    @isolate("message")
    async def handler() -> None:
        calls.append("ran")
        raise ValueError("unexpected payload")

    await handler()

    assert calls == ["ran"]


async def test_a_database_outage_is_left_to_propagate() -> None:
    """The one exception to the isolation, and the reason it is not blanket.

    discord.py routes it to `Client.on_error`, where the runner turns it into the
    non-zero exit section 3 of the spec calls for. Swallowing it here would leave a
    worker connected to a gateway and writing nowhere.
    """

    @isolate("message")
    async def handler() -> None:
        raise OperationalError("INSERT", {}, Exception("server closed the connection"))

    with pytest.raises(OperationalError):
        await handler()


async def test_the_wrapper_keeps_the_handler_identity() -> None:
    """`functools.wraps`, so discord.py still sees the event name it dispatches on."""

    @isolate("raw_message_edit")
    async def on_raw_message_edit() -> None:
        return None

    assert on_raw_message_edit.__name__ == "on_raw_message_edit"


async def test_a_successful_handler_is_untouched() -> None:
    seen: list[int] = []

    @isolate("message")
    async def handler(value: int) -> None:
        seen.append(value)

    await handler(7)

    assert seen == [7]
