"""Proves the rollback-per-test harness actually rolls back.

These two tests are a pair, and they depend on running in this order — the first
commits, the second checks the commit did not survive. Keep them in this file, in this
order: split apart or reordered, they prove nothing. Everything else in the suite
trusts them, since a harness that leaks writes turns every later test into a coin flip
decided by collection order.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PROBE_TABLE = "harness_probe"


@pytest.mark.db
async def test_a_a_commit_inside_a_test_is_visible_to_that_test(
    db_session: AsyncSession,
) -> None:
    """The code under test can commit and read its own writes back."""
    await db_session.execute(
        text(f"CREATE TABLE {PROBE_TABLE} (id integer primary key)")
    )
    await db_session.execute(text(f"INSERT INTO {PROBE_TABLE} (id) VALUES (1)"))
    await db_session.commit()

    count = await db_session.scalar(text(f"SELECT count(*) FROM {PROBE_TABLE}"))
    assert count == 1


@pytest.mark.db
async def test_b_that_commit_did_not_outlive_the_test(db_session: AsyncSession) -> None:
    """...and none of it reaches the next test.

    DDL is transactional in Postgres, so the table itself is gone too — which makes
    `to_regclass` the sharpest probe available: it answers NULL for a name no relation
    carries.
    """
    still_there = await db_session.scalar(
        text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{PROBE_TABLE}"}
    )
    assert still_there is False
