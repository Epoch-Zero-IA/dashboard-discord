from typing import Literal

import msgspec
from litestar import Controller, get

from backend.security import require_api_key


class Greeting(msgspec.Struct):
    message: str


class HealthCheck(msgspec.Struct):
    status: Literal["ok"] = "ok"


class ApiController(Controller):
    """Groups related routes. The /api prefix is applied by the root router in app.py,
    not here, so every controller stays prefix-agnostic. Add shared `guards`,
    `dependencies` here later."""

    # Guarded by an API key; `health` stays open because the compose healthcheck
    # reaches it directly, without going through nginx.
    @get(
        "/hello",
        name="api:hello",
        guards=[require_api_key],
        security=[{"APIKey": []}],
    )
    async def hello(self) -> Greeting:
        return Greeting(message="Hello from Litestar")

    @get("/health", name="api:health")
    async def health_check(self) -> HealthCheck:
        return HealthCheck()
