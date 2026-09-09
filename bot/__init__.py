"""The Discord worker: listens to the gateway, writes through `core`.

Knows nothing of Litestar. Runs as a single replica, definitively — two instances
connected to the same gateway handle every message twice, and nothing in the protocol
lets them share the work. That constraint is what makes the scheduled jobs here safe
without any distributed lock.

The shape of this package follows one rule: **everything that decides something is a
pure function, and everything that talks to discord.py is thin.** `adapters` converts
gateway objects into `core.records` dataclasses at the boundary and nothing downstream
ever sees a `discord.Message`, so the interesting logic is testable without faking a
library we do not control.
"""
