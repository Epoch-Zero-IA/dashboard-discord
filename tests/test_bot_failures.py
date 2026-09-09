"""Telling a database outage apart from a bug of ours.

The classification decides whether the worker exits — and an exit is a restart. Getting
it wrong in one direction gives a restart loop that ingests nothing; in the other, a
worker that stays up writing to a database that is gone.
"""

from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)

from bot.failures import is_database_unavailable


def test_a_refused_socket_is_an_outage() -> None:
    """What a stopped Postgres raises before SQLAlchemy has anything to wrap."""
    assert is_database_unavailable(ConnectionRefusedError(111, "Connection refused"))


def test_an_operational_error_is_an_outage() -> None:
    assert is_database_unavailable(OperationalError("SELECT 1", {}, Exception("gone")))


def test_an_interface_error_is_an_outage() -> None:
    assert is_database_unavailable(InterfaceError("SELECT 1", {}, Exception("closed")))


def test_an_invalidated_connection_is_an_outage() -> None:
    """SQLAlchemy's own verdict that the connection died under the statement."""
    error = DBAPIError("SELECT 1", {}, Exception("boom"), connection_invalidated=True)
    assert is_database_unavailable(error)


def test_a_constraint_violation_is_not_an_outage() -> None:
    """Our bug, not Postgres's absence — exiting on it would loop forever."""
    error = IntegrityError("INSERT", {}, Exception("duplicate key"))
    assert not is_database_unavailable(error)


def test_a_broken_statement_is_not_an_outage() -> None:
    error = ProgrammingError("SELECT nope", {}, Exception("no such column"))
    assert not is_database_unavailable(error)


def test_an_ordinary_bug_is_not_an_outage() -> None:
    assert not is_database_unavailable(TypeError("NoneType is not subscriptable"))
