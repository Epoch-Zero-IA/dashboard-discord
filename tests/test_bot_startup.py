"""The intents check, and the two-flags-per-intent trap it exists for."""

import pytest

from bot.startup import (
    GUILD_MEMBERS,
    MESSAGE_CONTENT,
    ensure_intents_enabled,
    missing_privileged_intents,
)
from core.config import MisconfiguredError

ALL_ENABLED = {
    "message_content": True,
    "message_content_limited": False,
    "guild_members": True,
    "guild_members_limited": False,
}
NONE_ENABLED = dict.fromkeys(ALL_ENABLED, False)
# What our own application actually looks like: below a hundred guilds, so Discord sets
# the `_limited` flag and leaves the reviewed one off.
UNDER_A_HUNDRED_GUILDS = {
    "message_content": False,
    "message_content_limited": True,
    "guild_members": False,
    "guild_members_limited": True,
}


def test_the_reviewed_flags_are_enough() -> None:
    assert missing_privileged_intents(**ALL_ENABLED) == []


def test_the_limited_flags_are_enough_too() -> None:
    """The regression this module was written for.

    A check reading only the reviewed flag would report both intents missing here and
    refuse to start — for the one configuration this project will actually run in.
    """
    assert missing_privileged_intents(**UNDER_A_HUNDRED_GUILDS) == []


def test_nothing_enabled_reports_both() -> None:
    assert missing_privileged_intents(**NONE_ENABLED) == [
        MESSAGE_CONTENT,
        GUILD_MEMBERS,
    ]


def test_one_missing_intent_is_named_alone() -> None:
    flags = {**ALL_ENABLED, "guild_members": False}
    assert missing_privileged_intents(**flags) == [GUILD_MEMBERS]


def test_starting_up_without_the_intents_raises_and_says_where_to_click() -> None:
    """The message has to point at the developer portal: the fix is not in the code."""
    with pytest.raises(MisconfiguredError, match="Privileged Gateway Intents"):
        ensure_intents_enabled(**NONE_ENABLED)


def test_starting_up_with_the_intents_is_silent() -> None:
    ensure_intents_enabled(**UNDER_A_HUNDRED_GUILDS)
