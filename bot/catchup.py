"""Catching a channel up between two cursors. One component, two uses.

The initial backfill and a gateway gap are the same problem in opposite directions:
fetch what we do not have, page by page, and be resumable. So there is one machine here
rather than two, and the direction is an argument.

Three properties, each one load-bearing:

- **One channel at a time, no `gather`.** discord.py already handles the 429s and the
  backoff; not parallelising is the only part left to us. Fifty channels fetched at once
  would spend their time being rate-limited.
- **The cursor is written after every page.** An interruption — a redeploy, a database
  outage, a SIGTERM — costs at most the hundred messages of the page in flight, and the
  next run picks up where this one stopped.
- **The decisions are pure.** What to fetch next, and how the cursor moves, are
  functions over values. Only the loop touches Discord and the database, and it takes
  its message source through a protocol a list can satisfy.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.queries import load_cursor
from core.records import CursorRecord, MessageEvent
from core.upserts import ingest_message, save_cursor

log = structlog.get_logger(__name__)

# Discord's own maximum for a history request. Asking for less costs more round trips
# for the same messages.
PAGE_SIZE = 100


class Direction(StrEnum):
    """Which way a catch-up walks."""

    # The initial backfill: from what we know towards the beginning of the channel.
    BACKWARD = "backward"
    # A gateway gap: from the newest message we hold towards now.
    FORWARD = "forward"


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """One page to ask Discord for."""

    channel_id: int
    limit: int
    before: int | None = None
    after: int | None = None


@dataclass(frozen=True, slots=True)
class CursorAdvance:
    """How a cursor moves after a page. `None` means "leave that field alone"."""

    oldest_message_id: int | None = None
    newest_message_id: int | None = None
    is_complete: bool | None = None


class MessageSource(Protocol):
    """Where a page of history comes from.

    A protocol, not the discord.py channel: the loop is then testable against a list,
    and the only thing that knows about `channel.history()` is the adapter that
    implements this in `bot.runner`.
    """

    async def fetch(self, request: FetchRequest) -> list[MessageEvent]:
        """Return the messages of one page, newest first.

        Args:
            request: The page to fetch.

        Returns:
            The messages, at most `request.limit` of them.
        """
        ...


def next_request(
    cursor: CursorRecord | None,
    channel_id: int,
    direction: Direction,
    page_size: int = PAGE_SIZE,
) -> FetchRequest | None:
    """Decide the next page to fetch, or that there is nothing left.

    Args:
        cursor: The channel's cursor, None for a channel never ingested.
        channel_id: The channel to catch up.
        direction: Which way to walk.
        page_size: How many messages to ask for.

    Returns:
        The request, or None when this direction is exhausted.
    """
    if direction is Direction.BACKWARD:
        # `is_complete` means the backfill has already reached the start of the
        # channel. There is no more past to walk into, ever.
        if cursor is not None and cursor.is_complete:
            return None
        before = cursor.oldest_message_id if cursor else None
        return FetchRequest(channel_id=channel_id, limit=page_size, before=before)

    # Forward without an anchor would mean "everything since the beginning of time",
    # which is the backfill's job and not a gap to fill.
    if cursor is None or cursor.newest_message_id is None:
        return None
    return FetchRequest(
        channel_id=channel_id, limit=page_size, after=cursor.newest_message_id
    )


def cursor_advance(
    cursor: CursorRecord | None,
    direction: Direction,
    message_ids: Sequence[int],
    page_size: int = PAGE_SIZE,
) -> CursorAdvance:
    """Work out how the cursor moves after a page of `message_ids`.

    Args:
        cursor: The cursor as it was before the page.
        direction: Which way the catch-up is walking.
        message_ids: The ids of the page just ingested, in any order.
        page_size: The page size that was requested.

    Returns:
        The fields to write, the others left alone.
    """
    if not message_ids:
        # An empty page walking backwards is the beginning of the channel. Walking
        # forwards it only means "nothing new", which moves nothing.
        return CursorAdvance(
            is_complete=True if direction is Direction.BACKWARD else None
        )

    if direction is Direction.FORWARD:
        return CursorAdvance(newest_message_id=max(message_ids))

    # A short page means Discord had no more to give: the start of the channel.
    return CursorAdvance(
        oldest_message_id=min(message_ids),
        # The very first backward page also anchors the forward cursor, otherwise a
        # gateway gap would have no idea where the known history ends.
        newest_message_id=(
            max(message_ids)
            if cursor is None or cursor.newest_message_id is None
            else None
        ),
        is_complete=len(message_ids) < page_size,
    )


async def catch_up_channel(
    session_factory: async_sessionmaker[AsyncSession],
    source: MessageSource,
    channel_id: int,
    direction: Direction,
    *,
    page_size: int = PAGE_SIZE,
    max_pages: int | None = None,
) -> int:
    """Walk one channel until the direction is exhausted.

    Each page is ingested and the cursor written in the same transaction, so the two
    can never disagree: either the page and its cursor are both in, or neither is.

    Args:
        session_factory: Where each page gets its session.
        source: Where the pages come from.
        channel_id: The channel to catch up.
        direction: Which way to walk.
        page_size: How many messages per page.
        max_pages: Stop after this many pages. For the startup catch-up, which must not
            hold the gateway connection for an hour on the first run.

    Returns:
        How many messages were ingested.
    """
    ingested = 0
    pages = 0

    while max_pages is None or pages < max_pages:
        async with session_factory() as session:
            cursor = await load_cursor(session, channel_id)
            request = next_request(cursor, channel_id, direction, page_size)
            if request is None:
                return ingested

            events = await source.fetch(request)
            for event in events:
                await ingest_message(session, event)

            advance = cursor_advance(
                cursor, direction, [event.message.id for event in events], page_size
            )
            await save_cursor(
                session,
                channel_id,
                oldest_message_id=advance.oldest_message_id,
                newest_message_id=advance.newest_message_id,
                is_complete=advance.is_complete,
            )
            await session.commit()

        ingested += len(events)
        pages += 1
        log.info(
            "catch_up_page",
            channel_id=channel_id,
            direction=direction,
            messages=len(events),
            total=ingested,
        )

        if not events:
            return ingested

    return ingested


async def catch_up_channels(
    session_factory: async_sessionmaker[AsyncSession],
    source: MessageSource,
    channel_ids: Sequence[int],
    direction: Direction,
    *,
    page_size: int = PAGE_SIZE,
    max_pages: int | None = None,
) -> int:
    """Catch several channels up, strictly one after the other.

    The sequential loop *is* the rate-limit strategy. Replacing it with a `gather` would
    turn fifty channels into fifty concurrent history requests, and discord.py would
    spend the run backing off.

    Args:
        session_factory: Where each page gets its session.
        source: Where the pages come from.
        channel_ids: The channels to catch up.
        direction: Which way to walk.
        page_size: How many messages per page.
        max_pages: Page budget, per channel.

    Returns:
        How many messages were ingested in total.
    """
    total = 0
    for channel_id in channel_ids:
        total += await catch_up_channel(
            session_factory,
            source,
            channel_id,
            direction,
            page_size=page_size,
            max_pages=max_pages,
        )
    return total
