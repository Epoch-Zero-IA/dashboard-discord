import os
import secrets

from litestar.connection import ASGIConnection, Request
from litestar.exceptions import ImproperlyConfiguredException, NotAuthorizedException
from litestar.handlers.base import BaseRouteHandler

API_KEY_HEADER = "X-API-Key"
API_KEY_ENV_VAR = "API_KEY"
REAL_IP_HEADER = "X-Real-IP"


def require_api_key(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Reject the request unless it carries the configured API key.

    The key never reaches the browser: nginx injects the header server-side (and the
    Vite dev proxy does the same in development), so the bundle stays free of secrets.
    Note what this proves — the call came through our proxy, or from a client holding
    the key. It says nothing about *which user* is calling.

    Read from the environment on every call rather than at import time, so tests and
    key rotation do not need a restart.

    Raises:
        NotAuthorizedException: If the key is missing, wrong, or not configured.
    """
    expected = os.getenv(API_KEY_ENV_VAR, "")
    provided = connection.headers.get(API_KEY_HEADER, "")

    # Fail closed: an unset API_KEY locks the route instead of opening it, so a
    # misconfigured deployment cannot silently serve the data to anyone.
    # compare_digest keeps the comparison constant-time, out of reach of timing attacks.
    if not expected or not secrets.compare_digest(provided, expected):
        raise NotAuthorizedException(detail="Invalid or missing API key")


def ensure_api_key_configured() -> None:
    """Refuse to start when the API key is missing, instead of failing silently.

    Without this check the app starts happily and answers 401 to every guarded call:
    the frontend looks broken with nothing pointing at the cause. Two things make that
    diagnosis harder than it should be — `${API_KEY:?}` does not block a Coolify
    deployment the way it blocks plain `docker compose`, and nginx drops a header whose
    value is empty rather than sending a blank one, so the API cannot tell a
    misconfigured proxy from an anonymous caller.

    Failing at startup also propagates: the container never becomes healthy, so
    `depends_on: service_healthy` keeps the frontend down and the deployment reports
    the failure instead of serving a broken app.

    Raises:
        ImproperlyConfiguredException: If the key is unset or empty.
    """
    if not os.getenv(API_KEY_ENV_VAR):
        msg = (
            f"{API_KEY_ENV_VAR} is unset or empty. Set it in the environment "
            f"(.env locally, project variables on Coolify) — the guard on the API "
            f"routes has nothing to compare against and would reject every call."
        )
        raise ImproperlyConfiguredException(msg)


def identify_client(request: Request) -> str:
    """Return the address a rate limit should be counted against.

    Litestar's default reads `request.client`, which behind nginx is the proxy for
    every visitor alike — one shared quota rather than one per client. nginx sets
    X-Real-IP from the address `real_ip` recovered, and `proxy_set_header` overwrites
    whatever the caller sent, so the header cannot be forged to dodge the limit.

    Returns:
        The client address, falling back to the connection when no proxy is in front.
    """
    forwarded = request.headers.get(REAL_IP_HEADER)
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"
