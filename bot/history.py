"""The discord.py side of `bot.catchup.MessageSource`.

Isolated here so that the catch-up loop never imports discord.py, and so the only code
that knows `channel.history()` exists is twenty lines long.
"""

import discord
import structlog

from bot.adapters import message_event
from bot.catchup import FetchRequest
from core.records import MessageEvent

log = structlog.get_logger(__name__)


class HistorySource:
    """Fetches pages of history through the connected client."""

    def __init__(self, client: discord.Client) -> None:
        """Store the client the pages are fetched with.

        Args:
            client: The connected gateway client.
        """
        self._client = client

    async def fetch(self, request: FetchRequest) -> list[MessageEvent]:
        """Return one page of a channel's history.

        A channel we cannot read is an empty page rather than an error: the bot may sit
        in a guild with fifty channels and rights on forty of them, and that must not
        stop the catch-up of the others. Anything that is neither a text channel nor a
        thread — a voice channel, a category, an id we no longer see — is empty too.

        Args:
            request: The page to fetch.

        Returns:
            The messages of the page, direct messages and unreadable channels excluded.
        """
        channel = self._client.get_channel(request.channel_id)
        # Threads too: they hold messages exactly like a channel and expose the same
        # `history()`. Accepting only TextChannel silently returned an empty page for
        # every thread, which the catch-up would then have read as "nothing older".
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return []

        before = discord.Object(id=request.before) if request.before else None
        after = discord.Object(id=request.after) if request.after else None

        events: list[MessageEvent] = []
        try:
            async for message in channel.history(
                limit=request.limit, before=before, after=after
            ):
                event = message_event(message)
                if event is not None:
                    events.append(event)
        except discord.Forbidden:
            log.info("channel_unreadable", channel_id=request.channel_id)
            return []
        return events
