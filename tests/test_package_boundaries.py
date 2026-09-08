"""Locks the two import rules the three-package split exists to enforce.

Both are invisible at runtime until the day an image is built: nothing fails when `core`
imports Litestar, it just means the bot image now carries a web framework, and that the
schema has become answerable to one. Cheap to check, expensive to discover later.

Each check runs in a subprocess: this test session has already imported `backend`, and
therefore Litestar, so asking the current interpreter would always fail.
"""

import subprocess
import sys

import pytest


def _imported_roots(modules: list[str], forbidden: tuple[str, ...]) -> str:
    """Import `modules` in a fresh interpreter and report the forbidden roots pulled in.

    Args:
        modules: The modules to import.
        forbidden: Top-level package names that must not appear.

    Returns:
        The sorted list of offending roots, as printed by the subprocess.
    """
    probe = (
        "import sys\n"
        f"import {', '.join(modules)}\n"
        f"roots = {forbidden!r}\n"
        "print(sorted({m.split('.')[0] for m in sys.modules if m.split('.')[0] in roots}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("modules", "forbidden", "why"),
    [
        pytest.param(
            ["core.config", "core.db", "core.models", "core.upserts", "core.records"],
            ("litestar", "discord"),
            "core/ is imported by both halves: a dependency either way round ships the "
            "wrong stack in the wrong image",
            id="core-imports-neither-stack",
        ),
        pytest.param(
            ["bot.runner", "bot.adapters", "bot.startup"],
            ("litestar",),
            "bot/ must stay installable from the `bot` dependency group alone, which "
            "has no Litestar in it",
            id="bot-imports-no-litestar",
        ),
    ],
)
def test_the_packages_keep_to_their_dependencies(
    modules: list[str], forbidden: tuple[str, ...], why: str
) -> None:
    assert _imported_roots(modules, forbidden) == "[]", why
