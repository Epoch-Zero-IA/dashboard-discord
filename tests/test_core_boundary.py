"""Locks the one architectural rule `core/` exists to enforce."""

import subprocess
import sys

FORBIDDEN_ROOTS = ("litestar", "discord")


def test_core_pulls_in_neither_litestar_nor_discord() -> None:
    """Importing `core` must not drag a web framework or a Discord client with it.

    `core/` is imported by both halves of the project, so a dependency either way
    round would put Litestar in the bot image and discord.py in the API image — and
    would make the schema answerable to a web framework. The check runs in a
    subprocess because this test session has already imported `backend`, and therefore
    Litestar: asking the current interpreter would always fail.
    """
    probe = (
        "import sys\n"
        "import core.config, core.db\n"
        f"roots = {FORBIDDEN_ROOTS!r}\n"
        "print(sorted({m.split('.')[0] for m in sys.modules if m.split('.')[0] in roots}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]", (
        f"core/ now imports {result.stdout.strip()} — see the module docstring of core/__init__.py"
    )
