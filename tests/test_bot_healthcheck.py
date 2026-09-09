"""Whether a heartbeat counts as healthy."""

import datetime as dt

from bot.healthcheck import MAX_BEAT_AGE, is_fresh

NOW = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)


def test_a_recent_beat_is_healthy() -> None:
    assert is_fresh(NOW - dt.timedelta(seconds=5), NOW)


def test_a_beat_at_the_limit_is_still_healthy() -> None:
    """Inclusive on purpose: a check landing exactly on the boundary must not flap."""
    assert is_fresh(NOW - MAX_BEAT_AGE, NOW)


def test_a_stale_beat_is_unhealthy() -> None:
    """The case an HTTP healthcheck would miss: process alive, heartbeat frozen."""
    assert not is_fresh(NOW - MAX_BEAT_AGE - dt.timedelta(seconds=1), NOW)


def test_no_beat_at_all_is_unhealthy() -> None:
    """True for a few seconds on a first start, which `start_period` covers."""
    assert not is_fresh(None, NOW)


def test_three_missed_beats_is_the_threshold() -> None:
    """Documents the number rather than leaving it to be rediscovered."""
    assert MAX_BEAT_AGE == dt.timedelta(seconds=90)
