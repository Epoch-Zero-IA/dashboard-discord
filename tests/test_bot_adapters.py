"""The two decisions of the discord.py boundary that are worth testing on their own.

The conversions themselves are deliberately thin and untested: they would need fake
`discord.Message` objects, which is exactly what the boundary exists to avoid.
"""

from datetime import UTC, datetime

from bot.adapters import content_edit, emoji_key


def test_a_unicode_emoji_is_its_own_key() -> None:
    assert emoji_key("👍", None) == "👍"


def test_a_custom_emoji_carries_its_id() -> None:
    """Two guilds can both have an `:aww:`; only the id tells them apart."""
    assert emoji_key("aww", 1234567890123456789) == "aww:1234567890123456789"


def test_an_edit_payload_yields_content_and_timestamp() -> None:
    data = {"content": "corrigé", "edited_timestamp": "2026-09-08T12:30:00+00:00"}

    edit = content_edit(data)

    assert edit == ("corrigé", datetime(2026, 9, 8, 12, 30, tzinfo=UTC))


def test_an_update_without_content_is_not_an_edit() -> None:
    """The case that would blank a message.

    Most `MESSAGE_UPDATE` events are not content edits — an embed resolved, a pin, a
    flag flipped — and carry only the fields that changed. Treating an absent `content`
    as an empty one would erase the text we already hold.
    """
    assert content_edit({"pinned": True}) is None


def test_an_update_without_an_edit_timestamp_is_not_an_edit() -> None:
    """Discord stamps every real user edit; anything else is Discord's own doing."""
    assert content_edit({"content": "inchangé", "edited_timestamp": None}) is None
