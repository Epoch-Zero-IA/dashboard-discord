"""The catch-up machinery: the pure decisions first, then the loop on a real database."""

from collections.abc import Sequence

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.catchup import (
    Direction,
    FetchRequest,
    catch_up_channel,
    cursor_advance,
    next_request,
)
from core.models import Message
from core.queries import load_cursor
from core.records import CursorRecord, MessageEvent
from tests.factories import CHANNEL_ID, an_event

PAGE = 3

# ---------------------------------------------------------------------------
# Tier one: what to fetch next, and how the cursor moves. No database, no Discord.
# ---------------------------------------------------------------------------


def test_a_channel_never_ingested_is_read_from_the_present_backwards() -> None:
    """No cursor means no anchor: Discord serves the most recent page."""
    request = next_request(None, CHANNEL_ID, Direction.BACKWARD, PAGE)

    assert request == FetchRequest(channel_id=CHANNEL_ID, limit=PAGE, before=None)


def test_a_backfill_resumes_from_the_oldest_message_it_has() -> None:
    cursor = CursorRecord(channel_id=CHANNEL_ID, oldest_message_id=500)

    request = next_request(cursor, CHANNEL_ID, Direction.BACKWARD, PAGE)

    assert request == FetchRequest(channel_id=CHANNEL_ID, limit=PAGE, before=500)


def test_a_completed_backfill_asks_for_nothing() -> None:
    """`is_complete` is final: the channel has no more past."""
    cursor = CursorRecord(channel_id=CHANNEL_ID, oldest_message_id=1, is_complete=True)

    assert next_request(cursor, CHANNEL_ID, Direction.BACKWARD, PAGE) is None


def test_a_gap_is_filled_from_the_newest_message_we_hold() -> None:
    cursor = CursorRecord(channel_id=CHANNEL_ID, newest_message_id=900)

    request = next_request(cursor, CHANNEL_ID, Direction.FORWARD, PAGE)

    assert request == FetchRequest(channel_id=CHANNEL_ID, limit=PAGE, after=900)


@pytest.mark.parametrize(
    "cursor",
    [None, CursorRecord(channel_id=CHANNEL_ID, oldest_message_id=10)],
    ids=["no-cursor", "cursor-without-a-forward-anchor"],
)
def test_walking_forward_without_an_anchor_does_nothing(
    cursor: CursorRecord | None,
) -> None:
    """Otherwise "forward" would mean the whole history, which is the backfill's job."""
    assert next_request(cursor, CHANNEL_ID, Direction.FORWARD, PAGE) is None


def test_a_full_backward_page_moves_the_oldest_cursor_and_keeps_going() -> None:
    advance = cursor_advance(None, Direction.BACKWARD, [30, 10, 20], PAGE)

    assert advance.oldest_message_id == 10
    assert advance.is_complete is False


def test_a_short_backward_page_is_the_start_of_the_channel() -> None:
    """Discord had less than a page to give, so there is nothing older."""
    advance = cursor_advance(None, Direction.BACKWARD, [30, 20], PAGE)

    assert advance.is_complete is True


def test_the_first_backward_page_also_anchors_the_forward_cursor() -> None:
    """Without this, a gateway gap would not know where the known history ends."""
    advance = cursor_advance(None, Direction.BACKWARD, [30, 10, 20], PAGE)

    assert advance.newest_message_id == 30


def test_a_later_backward_page_leaves_the_forward_cursor_alone() -> None:
    """It walks into the past: the newest message we hold has not changed."""
    cursor = CursorRecord(
        channel_id=CHANNEL_ID, oldest_message_id=10, newest_message_id=30
    )

    advance = cursor_advance(cursor, Direction.BACKWARD, [9, 8, 7], PAGE)

    assert advance.newest_message_id is None
    assert advance.oldest_message_id == 7


def test_an_empty_backward_page_completes_the_backfill() -> None:
    assert cursor_advance(None, Direction.BACKWARD, [], PAGE).is_complete is True


def test_a_forward_page_only_moves_the_newest_cursor() -> None:
    cursor = CursorRecord(channel_id=CHANNEL_ID, newest_message_id=30)

    advance = cursor_advance(cursor, Direction.FORWARD, [31, 33, 32], PAGE)

    assert advance.newest_message_id == 33
    assert advance.oldest_message_id is None
    assert advance.is_complete is None


def test_an_empty_forward_page_moves_nothing() -> None:
    """Nothing new is not the same as the end of the channel."""
    cursor = CursorRecord(channel_id=CHANNEL_ID, newest_message_id=30)

    advance = cursor_advance(cursor, Direction.FORWARD, [], PAGE)

    assert advance == cursor_advance(cursor, Direction.FORWARD, [], PAGE)
    assert advance.newest_message_id is None
    assert advance.is_complete is None


# ---------------------------------------------------------------------------
# Tier two: the loop, against a real database and a list standing in for Discord.
# ---------------------------------------------------------------------------


class FakeHistory:
    """A channel's history as a list, answering `before` and `after` like Discord does.

    This is the whole point of `MessageSource` being a protocol: the loop can be walked
    end to end, page by page, with no network and no library to fake.
    """

    def __init__(self, message_ids: Sequence[int]) -> None:
        """Store the history, newest first.

        Args:
            message_ids: Every message the channel holds.
        """
        self.message_ids = sorted(message_ids, reverse=True)
        self.requests: list[FetchRequest] = []

    async def fetch(self, request: FetchRequest) -> list[MessageEvent]:
        """Return one page, newest first.

        Args:
            request: The page to fetch.

        Returns:
            At most `request.limit` messages.
        """
        self.requests.append(request)
        ids = self.message_ids
        if request.before is not None:
            ids = [i for i in ids if i < request.before]
        if request.after is not None:
            ids = [i for i in ids if i > request.after]
        return [an_event(message_id=i) for i in ids[: request.limit]]


@pytest.mark.db
async def test_a_backfill_walks_the_whole_channel_and_stops(
    db_engine: AsyncEngine,
) -> None:
    """Every message once, no duplicates, and the cursor left marked complete."""
    history = FakeHistory([1, 2, 3, 4, 5, 6, 7])
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    ingested = await catch_up_channel(
        factory, history, CHANNEL_ID, Direction.BACKWARD, page_size=PAGE
    )

    assert ingested == 7
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 7
        cursor = await load_cursor(session, CHANNEL_ID)
    assert cursor is not None
    assert cursor.is_complete is True
    assert cursor.oldest_message_id == 1
    assert cursor.newest_message_id == 7


@pytest.mark.db
async def test_a_backfill_resumes_where_it_was_interrupted(
    db_engine: AsyncEngine,
) -> None:
    """One page at a time, then a resume: no gap, no duplicate.

    This is the property the whole component exists for. A redeploy in the middle of a
    backfill costs the page in flight and nothing else.
    """
    history = FakeHistory([1, 2, 3, 4, 5, 6, 7])
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    first = await catch_up_channel(
        factory, history, CHANNEL_ID, Direction.BACKWARD, page_size=PAGE, max_pages=1
    )
    rest = await catch_up_channel(
        factory, history, CHANNEL_ID, Direction.BACKWARD, page_size=PAGE
    )

    assert first == PAGE
    assert first + rest == 7
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 7


@pytest.mark.db
async def test_replaying_a_finished_backfill_fetches_nothing(
    db_engine: AsyncEngine,
) -> None:
    """Idempotent, and cheap: a completed cursor asks Discord for nothing at all."""
    history = FakeHistory([1, 2, 3])
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await catch_up_channel(
        factory, history, CHANNEL_ID, Direction.BACKWARD, page_size=PAGE
    )
    requests_so_far = len(history.requests)

    again = await catch_up_channel(
        factory, history, CHANNEL_ID, Direction.BACKWARD, page_size=PAGE
    )

    assert again == 0
    assert len(history.requests) == requests_so_far


@pytest.mark.db
async def test_a_gap_is_filled_forward_from_the_cursor(db_engine: AsyncEngine) -> None:
    """The gateway-gap case, on the same machinery as the backfill."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await catch_up_channel(
        factory, FakeHistory([1, 2, 3]), CHANNEL_ID, Direction.BACKWARD, page_size=PAGE
    )

    # Three messages posted while the worker was down.
    ingested = await catch_up_channel(
        factory,
        FakeHistory([1, 2, 3, 4, 5, 6]),
        CHANNEL_ID,
        Direction.FORWARD,
        page_size=PAGE,
    )

    assert ingested == 3
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 6
        cursor = await load_cursor(session, CHANNEL_ID)
    assert cursor is not None
    assert cursor.newest_message_id == 6
