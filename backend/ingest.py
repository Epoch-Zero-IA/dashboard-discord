"""The endpoint that proves the ingestion works.

This is the acceptance criterion of the socle, and nothing more: it answers "is the
worker alive, and how far has it got in each channel". The dashboard of piece B will
define its own surface over `daily_activity`; this one is for us.
"""

from datetime import datetime

import msgspec
from litestar import Controller, get
from litestar.di import NamedDependency
from sqlalchemy.ext.asyncio import AsyncSession

from backend.security import require_api_key
from core.queries import channel_ingest_status, last_heartbeat


class ChannelStatus(msgspec.Struct):
    """How far the ingestion has got in one channel."""

    channel_id: int
    channel_name: str
    message_count: int
    oldest_message_id: int | None
    newest_message_id: int | None
    is_complete: bool


class WorkerHeartbeat(msgspec.Struct):
    """The worker's last proof of life."""

    beat_at: datetime
    session_started_at: datetime


class IngestStatus(msgspec.Struct):
    """The whole answer.

    A `msgspec.Struct` and not a `dict`: a `dict[str, str]` would come out of
    `openapi.json` as `{ [key: string]: string }`, which describes nothing and would
    leave the frontend with no types at all.
    """

    channels: list[ChannelStatus]
    total_messages: int
    heartbeat: WorkerHeartbeat | None


class IngestController(Controller):
    """Read-only view of the ingestion state."""

    # Guarded: the answer names every channel of the server, which is more than a
    # visitor needs. `security` is declared too, else openapi.json advertises the route
    # as open.
    @get(
        "/ingest/status",
        name="api:ingest-status",
        guards=[require_api_key],
        security=[{"APIKey": []}],
    )
    async def ingest_status(
        self, session: NamedDependency[AsyncSession]
    ) -> IngestStatus:
        """Return the ingestion state, per channel and overall.

        Args:
            session: The request's database session. Annotated as a
                `NamedDependency`, not a bare `AsyncSession`: Litestar still infers the
                latter but warns that it will stop doing so in 3.0.

        Returns:
            The state: one entry per known channel, the total message count, and the
            worker's last heartbeat — None when it has never run.
        """
        channels = await channel_ingest_status(session)
        beat = await last_heartbeat(session)

        return IngestStatus(
            channels=[
                ChannelStatus(
                    channel_id=channel.channel_id,
                    channel_name=channel.channel_name,
                    message_count=channel.message_count,
                    oldest_message_id=channel.oldest_message_id,
                    newest_message_id=channel.newest_message_id,
                    is_complete=channel.is_complete,
                )
                for channel in channels
            ],
            total_messages=sum(channel.message_count for channel in channels),
            heartbeat=(
                WorkerHeartbeat(
                    beat_at=beat.beat_at, session_started_at=beat.session_started_at
                )
                if beat
                else None
            ),
        )
