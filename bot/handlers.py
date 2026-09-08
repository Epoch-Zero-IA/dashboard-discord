"""Handler isolation: one bad event must not take the gateway down with it.

An unhandled exception in a handler is a normal occurrence — a message shaped in a way
we did not foresee, a channel we have no rights on — and a worker that dies on the first
one ingests nothing from the forty-nine other channels. So handlers are wrapped: the
error is logged with the event that caused it and the worker moves on.

The one exception is a database outage, which is precisely what must *not* be swallowed:
it propagates, discord.py hands it to `Client.on_error`, and the runner turns it into
the non-zero exit that section 3 of the spec calls for. This module is where the two
paths part.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec

import structlog

from bot.failures import is_database_unavailable

P = ParamSpec("P")
Handler = Callable[P, Awaitable[None]]

log = structlog.get_logger(__name__)


def isolate(event: str) -> Callable[[Handler[P]], Handler[P]]:
    """Wrap a gateway handler so its failures do not reach the connection.

    Args:
        event: The gateway event being handled, logged as-is so a recurring failure can
            be traced to one kind of event rather than to "the bot".

    Returns:
        A decorator.
    """

    def decorate(handler: Handler[P]) -> Handler[P]:
        @wraps(handler)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
            try:
                await handler(*args, **kwargs)
            except Exception as exc:
                # Re-raised, not handled here: the runner owns the shutdown, and doing
                # it from inside a handler would leave the gateway half-closed.
                if is_database_unavailable(exc):
                    raise
                log.exception("gateway_handler_failed", gateway_event=event)

        return wrapper

    return decorate
