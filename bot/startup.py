"""What has to be true before the worker is allowed to run.

The privileged intents are the reason this module exists. `MESSAGE_CONTENT` and
`GUILD_MEMBERS` are toggles in the Discord developer portal, and without them `content`
arrives empty and member arrivals are silent — no error, no warning, just a bot that
ingests nothing and looks healthy. Checking at startup and refusing to run turns the
worst failure mode of this project into a crash on line one, in the spirit of
`backend.security.ensure_api_key_configured`.

The check takes four booleans rather than the `discord.ApplicationFlags` object it will
be fed. That keeps it a pure function — the four reads happen at the boundary, in
`bot.runner` — and it is also the only signature the type checker accepts: discord.py
implements those flags as its own descriptors, which no Protocol of plain attributes
matches.
"""

from core.config import MisconfiguredError

MESSAGE_CONTENT = "message content"
GUILD_MEMBERS = "server members"


def missing_privileged_intents(
    *,
    message_content: bool,
    message_content_limited: bool,
    guild_members: bool,
    guild_members_limited: bool,
) -> list[str]:
    """Return the privileged intents that are not enabled for this application.

    Discord exposes **two** flags per privileged intent, and this is the trap the whole
    module is built around. The plain flag means approved after review, which an
    application needs past a hundred guilds; the `_limited` one means enabled for an
    application below that threshold — which is ours. Either is enough to receive the
    events, so a check that reads only the first would refuse to start a bot whose
    intents are perfectly enabled, and it would fail closed, which is to say
    convincingly.

    Args:
        message_content: `gateway_message_content`, the reviewed grant.
        message_content_limited: `gateway_message_content_limited`, the grant given
            below a hundred guilds.
        guild_members: `gateway_guild_members`.
        guild_members_limited: `gateway_guild_members_limited`.

    Returns:
        The human-readable names of the missing intents, empty when all is well.
    """
    missing: list[str] = []
    if not (message_content or message_content_limited):
        missing.append(MESSAGE_CONTENT)
    if not (guild_members or guild_members_limited):
        missing.append(GUILD_MEMBERS)
    return missing


def ensure_intents_enabled(
    *,
    message_content: bool,
    message_content_limited: bool,
    guild_members: bool,
    guild_members_limited: bool,
) -> None:
    """Refuse to run unless both privileged intents are enabled.

    Args:
        message_content: `gateway_message_content`.
        message_content_limited: `gateway_message_content_limited`.
        guild_members: `gateway_guild_members`.
        guild_members_limited: `gateway_guild_members_limited`.

    Raises:
        MisconfiguredError: If either intent is missing. The message names the toggle
            to flip, since the fix is in the developer portal and not in the code.
    """
    missing = missing_privileged_intents(
        message_content=message_content,
        message_content_limited=message_content_limited,
        guild_members=guild_members,
        guild_members_limited=guild_members_limited,
    )
    if not missing:
        return

    joined = " and ".join(missing)
    msg = (
        f"The privileged intent(s) {joined} are not enabled for this application. "
        f"Enable them under Bot > Privileged Gateway Intents in the Discord developer "
        f"portal, then restart. Without them message content arrives empty and member "
        f"arrivals are silent, with no error to point at the cause."
    )
    raise MisconfiguredError(msg)
