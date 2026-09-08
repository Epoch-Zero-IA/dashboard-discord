"""The gateway client. Thin by design: every decision lives in a module of its own.

Only one replica of this ever runs. Two clients on the same gateway receive every event
twice and there is nothing in the protocol to divide the work between them, so the
scheduled jobs below need no distributed lock — and the day the worker has to scale,
they have to move out.
"""

import asyncio
import datetime as dt
import sys
from typing import Any

import discord
import structlog
from discord.ext import tasks
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.adapters import content_edit, message_event, reaction_record
from bot.catchup import Direction, catch_up_channels
from bot.config import DEFAULT_NIGHTLY_HOUR, HEARTBEAT_SECONDS, nightly_hour
from bot.failures import is_database_unavailable
from bot.handlers import isolate
from bot.history import HistorySource
from bot.nightly import run_nightly
from bot.startup import ensure_intents_enabled
from core.upserts import (
    apply_message_edit,
    ingest_message,
    mark_message_deleted,
    record_heartbeat,
    remove_reaction,
    upsert_reaction,
)

log = structlog.get_logger(__name__)

# Exactly what the ingestion needs, and nothing more. `message_content` and `members`
# are the privileged ones: declared here, but only effective if they are also enabled
# in the developer portal — which is what setup_hook verifies.
INTENTS = discord.Intents(
    guilds=True,
    guild_messages=True,
    guild_reactions=True,
    message_content=True,
    members=True,
)

# Exit codes. The distinction is for whoever reads the logs: a restart fixes an outage
# and never fixes a misconfiguration.
EXIT_OK = 0
EXIT_DATABASE_UNAVAILABLE = 1
EXIT_MISCONFIGURED = 2

# Pages per channel per start, for the catch-up that runs on connection. Twenty pages is
# two thousand messages: enough to close any realistic redeploy gap, while a first
# backfill of a busy channel would otherwise hold the startup for an hour. What is left
# is picked up at the next start, or by `python -m bot backfill`, since the cursor
# survives.
STARTUP_PAGE_BUDGET = 20


class Worker(discord.Client):
    """Listens to the gateway and writes through `core.upserts`."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        backfill: bool = False,
    ) -> None:
        """Build the client.

        Args:
            session_factory: Where every write gets its session.
            backfill: Run the catch-up to completion and exit, instead of listening.
                This is `python -m bot backfill`, for the initial import.
        """
        super().__init__(intents=INTENTS)
        self._session_factory = session_factory
        self._backfill = backfill
        self._started_at = dt.datetime.now(dt.UTC)
        self._catch_up_task: asyncio.Task[None] | None = None
        self.exit_code = EXIT_OK

    async def setup_hook(self) -> None:
        """Verify the privileged intents, then start the scheduled jobs.

        Runs after the HTTP login and before the gateway connection, which is exactly
        when `application_info()` is available and nothing has been ingested yet: a
        missing intent aborts the start instead of producing a bot that reads empty
        messages.
        """
        flags = (await self.application_info()).flags
        ensure_intents_enabled(
            message_content=flags.gateway_message_content,
            message_content_limited=flags.gateway_message_content_limited,
            guild_members=flags.gateway_guild_members,
            guild_members_limited=flags.gateway_guild_members_limited,
        )
        self.heartbeat.start()
        # The schedule is read here and not at import: `tasks.loop` evaluates its
        # decorator when the class is defined, which would freeze the hour before any
        # environment is loaded.
        self.nightly.change_interval(time=dt.time(hour=nightly_hour(), tzinfo=dt.UTC))
        self.nightly.start()

    async def on_ready(self) -> None:
        """Log the connection, then close whatever gap the downtime left.

        `on_ready` fires again on every reconnection, hence the guard: a second
        catch-up running beside the first would fetch the same pages twice and fight
        over the same cursor.
        """
        log.info("gateway_ready", user=str(self.user), guilds=len(self.guilds))
        if self._catch_up_task is None or self._catch_up_task.done():
            self._catch_up_task = asyncio.create_task(self._catch_up())

    async def _catch_up(self) -> None:
        """Fill the gateway gap, then push the backfill a little further.

        Forward first: the messages posted while the worker was down are the ones the
        dashboard is missing right now. The backward walk is history, and it can wait
        for the next start — which is exactly what the page budget makes it do.

        A failure here is logged and dropped rather than raised: the cursors mean the
        work resumes by itself, and a failed catch-up must not cost the live ingestion
        that is already running.
        """
        channel_ids = [
            channel.id
            for channel in self.get_all_channels()
            if isinstance(channel, discord.TextChannel)
        ]
        source = HistorySource(self)
        # No budget in backfill mode: the point of that command is to finish.
        budget = None if self._backfill else STARTUP_PAGE_BUDGET
        try:
            for direction in (Direction.FORWARD, Direction.BACKWARD):
                ingested = await catch_up_channels(
                    self._session_factory,
                    source,
                    channel_ids,
                    direction,
                    max_pages=budget,
                )
                log.info("catch_up_done", direction=direction, messages=ingested)
            if self._backfill:
                log.info("backfill_complete", channels=len(channel_ids))
                await self.close()
        except Exception as exc:
            if await self._abort_if_database_gone(exc, "catch_up"):
                return
            log.exception("catch_up_failed")

    async def _abort_if_database_gone(self, exc: Exception, source: str) -> bool:
        """Shut the worker down if `exc` means the database is gone.

        Section 3 of the spec in one method, called from every place that writes: the
        gateway handlers, the heartbeat and the catch-up. Exit non-zero, let the
        orchestrator restart us, let the cursors refill the gap.

        Args:
            exc: The exception to judge.
            source: What was running, for the log line.

        Returns:
            True when the shutdown was started, so the caller can stop.
        """
        if not is_database_unavailable(exc):
            return False

        log.error("database_unavailable", source=source, error=str(exc))
        self.exit_code = EXIT_DATABASE_UNAVAILABLE
        # Closed properly rather than killed: an abrupt exit leaves the gateway session
        # half-open and slows the restart down.
        await self.close()
        return True

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        """Turn a database outage into a shutdown, and anything else into a log line.

        discord.py routes every exception a handler let through here. The handlers
        already swallow what they can (`bot.handlers.isolate`), so what arrives is
        either an outage — section 3 of the spec: exit non-zero, let the orchestrator
        restart us, let the cursors refill the gap — or a bug worth a stack trace.

        Args:
            event_method: The handler that raised.
            *args: The handler's positional arguments, unused.
            **kwargs: The handler's keyword arguments, unused.
        """
        exc = sys.exc_info()[1]
        if exc is not None and is_database_unavailable(exc):
            log.error(
                "database_unavailable", gateway_event=event_method, error=str(exc)
            )
            self.exit_code = EXIT_DATABASE_UNAVAILABLE
            # Closed properly rather than killed: an abrupt exit leaves the gateway
            # session half-open and slows the restart down.
            await self.close()
            return

        log.exception("gateway_error", gateway_event=event_method)

    @isolate("message")
    async def on_message(self, message: discord.Message) -> None:
        """Ingest a message and refresh the dimensions it arrived with.

        Args:
            message: The message posted.
        """
        event = message_event(message)
        if event is None:
            return

        async with self._session_factory() as session:
            await ingest_message(session, event)
            await session.commit()

    @isolate("raw_message_edit")
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Overwrite the content of an edited message.

        Raw, so that an edit to a message we never cached still lands. An edit for a
        message absent from the database is a no-op, not an error.

        Args:
            payload: The raw update event.
        """
        edit = content_edit(payload.data)
        if edit is None:
            return

        content, edited_at = edit
        async with self._session_factory() as session:
            applied = await apply_message_edit(
                session, payload.message_id, content, edited_at
            )
            await session.commit()

        if not applied:
            log.debug("edit_for_unknown_message", message_id=payload.message_id)

    @isolate("raw_message_delete")
    async def on_raw_message_delete(
        self, payload: discord.RawMessageDeleteEvent
    ) -> None:
        """Flag a deleted message and drop its content, keeping the row.

        Args:
            payload: The raw delete event.
        """
        async with self._session_factory() as session:
            applied = await mark_message_deleted(
                session, payload.message_id, dt.datetime.now(dt.UTC)
            )
            await session.commit()

        if not applied:
            log.debug("delete_for_unknown_message", message_id=payload.message_id)

    @isolate("raw_reaction_add")
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Record a reaction.

        Args:
            payload: The raw reaction event.
        """
        async with self._session_factory() as session:
            await upsert_reaction(session, reaction_record(payload))
            await session.commit()

    @isolate("raw_reaction_remove")
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Delete a reaction that was taken back.

        Args:
            payload: The raw reaction event.
        """
        async with self._session_factory() as session:
            await remove_reaction(session, reaction_record(payload))
            await session.commit()

    @tasks.loop(seconds=HEARTBEAT_SECONDS)
    async def heartbeat(self) -> None:
        """Write the proof of life the compose healthcheck reads.

        In the database rather than behind an HTTP port because it has to prove three
        things at once: the process lives, the gateway connection is up, and the
        database answers. A `/health` endpoint proves only the first.
        """
        now = dt.datetime.now(dt.UTC)
        try:
            async with self._session_factory() as session:
                await record_heartbeat(
                    session, beat_at=now, session_started_at=self._started_at
                )
                await session.commit()
        except Exception as exc:
            # `tasks.loop` would otherwise stop the loop and leave the worker alive
            # with a heartbeat frozen in the past — alive to the process manager,
            # invisible to the healthcheck, ingesting nothing.
            if await self._abort_if_database_gone(exc, "heartbeat"):
                return
            log.exception("heartbeat_failed")

    @heartbeat.before_loop
    async def _wait_until_connected(self) -> None:
        """Hold the heartbeat until the gateway is up.

        Otherwise the first beat is written before the connection exists, and claims a
        health the worker has not reached yet.
        """
        await self.wait_until_ready()

    @tasks.loop(time=dt.time(hour=DEFAULT_NIGHTLY_HOUR, tzinfo=dt.UTC))
    async def nightly(self) -> None:
        """Aggregate the day just ended, then purge what is past retention.

        Runs in this process, which is what the single-replica constraint buys: no
        distributed lock, no separate scheduler, and no way for two runs to overlap.
        The actual hour comes from the environment, set in `setup_hook`.
        """
        try:
            await run_nightly(
                self._session_factory, today=dt.datetime.now(dt.UTC).date()
            )
        except Exception as exc:
            if await self._abort_if_database_gone(exc, "nightly"):
                return
            # Logged and dropped: the run is replayable, so tomorrow night picks up
            # whatever this one left behind.
            log.exception("nightly_failed")

    @nightly.before_loop
    async def _wait_before_nightly(self) -> None:
        """Hold the nightly job until the gateway is up, like the heartbeat."""
        await self.wait_until_ready()
