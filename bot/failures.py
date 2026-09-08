"""Telling an outage apart from a bug, which the worker treats very differently.

Section 3 of the spec settles the behaviour when Postgres is unreachable: no memory
buffer, no long retry, exit non-zero and let the orchestrator restart us. The catch-up
cursors refill the gap, exactly as after a gateway gap.

That rule only holds if we apply it to actual outages. A broken SQL statement of ours
also raises from the database driver, and exiting on it would produce an endless restart
loop that ingests nothing and looks identical to an outage. So the two are classified
here, once, and the callers act accordingly: an outage kills the process, a bug is
logged and the worker carries on with the next event.
"""

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError


def is_database_unavailable(exc: BaseException) -> bool:
    """Report whether `exc` means the database itself is gone.

    Three signals, in order of precision:

    - `OSError` and its subclasses, which is what a refused or reset socket raises
      before SQLAlchemy has anything to wrap.
    - `DBAPIError.connection_invalidated`, SQLAlchemy's own verdict that the connection
      died under the statement.
    - `OperationalError` and `InterfaceError`, the two driver categories that cover a
      server that went away, a pool that cannot connect, and authentication that no
      longer works.

    Everything else — `IntegrityError`, `ProgrammingError`, a `TypeError` in our own
    code — is a bug on our side and deliberately not an outage.

    Args:
        exc: The exception to classify.

    Returns:
        True when the right response is to exit and be restarted.
    """
    if isinstance(exc, OSError):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, (OperationalError, InterfaceError))
