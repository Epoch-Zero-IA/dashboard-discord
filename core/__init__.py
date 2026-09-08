"""Schema owner, shared by the API and the Discord worker.

Depends on neither Litestar nor discord.py. Both halves of the project import this
package, so a dependency either way round would drag the wrong stack into the wrong
image — and, worse, would make the schema answerable to a web framework. A test locks
the boundary down (tests/test_core_boundary.py).
"""
