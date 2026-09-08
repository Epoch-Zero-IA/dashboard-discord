from litestar import Litestar, Router
from litestar.data_extractors import RequestExtractorField, ResponseExtractorField
from litestar.di import Provide
from litestar.middleware.rate_limit import DurationUnit, RateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.openapi.spec import Components, SecurityScheme
from litestar.plugins.structlog import (
    LoggingMiddlewareConfig,
    StructlogConfig,
    StructlogPlugin,
)
from litestar_granian import GranianPlugin
from litestar_vite import TypeGenConfig, ViteConfig, VitePlugin
from litestar_vite.config import PathConfig, RuntimeConfig

from backend import DOCS_ENABLED, FRONTEND_ROOT, OPENAPI_SCHEMA
from backend.db import db_lifespan, provide_session
from backend.exceptions import AppError, app_error_handler
from backend.ingest import IngestController
from backend.routes import ApiController
from backend.security import API_KEY_HEADER, ensure_api_key_configured, identify_client
from core.config import ensure_database_configured

# nginx serves the frontend, not Litestar: `enabled=False` makes the plugin inert at
# runtime (no HTML catch-all, no static files, no lifespan, no Vite process). The
# `litestar assets *` commands still work — `on_cli_init` is not short-circuited — so
# the plugin is now only a type generator. `mode` and `bundle_dir` drive that codegen
# and the Vite build, nothing else.
config = ViteConfig(
    enabled=False,
    mode="spa",
    runtime=RuntimeConfig(executor="pnpm"),
    paths=PathConfig(
        root=FRONTEND_ROOT,
        resource_dir=FRONTEND_ROOT / "src",
        bundle_dir=FRONTEND_ROOT / "dist",
    ),
    # openapi.json lands at the repo root to be versioned; the rest of
    # src/generated/ is derived and stays git-ignored.
    types=TypeGenConfig(generate_zod=True, openapi_path=OPENAPI_SCHEMA),
)
# Structured logging: pretty coloured console on a TTY (dev), JSON otherwise (prod),
# so logs ship straight to Loki/Datadog/ELK without re-parsing. Request/response bodies
# are dropped from the logged fields — they bloat logs and can leak secrets (tokens,
# PII); noisy infra routes are excluded too to keep logs signal. /api/health is one of
# them: the compose healthcheck hits it every 10s, some 8600 lines a day that would
# bury the real traffic.
# Annotated with the library's Literals: a bare `list[str]` would let a misspelled
# field through with nothing flagging it before runtime.
exclude = ["/schema", "/favicon.ico", "/api/health"]
response_log_fields: list[ResponseExtractorField] = [
    "status_code",
    "cookies",
    "headers",
]
request_log_fields: list[RequestExtractorField] = [
    "path",
    "method",
    "content_type",
    "headers",
    "cookies",
    "query",
    "path_params",
]

middleware_logging_config = LoggingMiddlewareConfig(
    exclude=exclude,
    request_log_fields=request_log_fields,
    response_log_fields=response_log_fields,
)
structlog_config = StructlogConfig(middleware_logging_config=middleware_logging_config)
structlog_plugin = StructlogPlugin(config=structlog_config)

# `static="auto"` would have nothing left to consume: the API serves no files.
# Worker count comes from WEB_CONCURRENCY, read natively by the Granian CLI.
plugins = [
    structlog_plugin,
    VitePlugin(config=config),
    GranianPlugin(),
]

# All Python routes live under /api to avoid collisions with the Svelte SPA (served
# at / by the Vite plugin). Register every controller here, not with a hardcoded prefix.
api_router = Router(
    path="/api",
    route_handlers=[ApiController, IngestController],
    # Injected here rather than per controller: every route that touches the database
    # wants the same session, and /api/health deliberately asks for none — binding it
    # to a session would make the healthcheck fail whenever Postgres is slow.
    dependencies={"session": Provide(provide_session)},
)


def build_openapi_config(*, docs_enabled: bool) -> OpenAPIConfig | None:
    """Return the OpenAPI configuration, or None to serve no documentation at all.

    Returning None is what actually removes the /schema router; `enabled_endpoints`
    would be the narrower knob but has been deprecated since Litestar 2.8. The
    trade-off is that the schema then leaves memory, so `litestar assets
    generate-types` needs the docs enabled — the `types` recipe forces it.

    Declaring the security scheme keeps openapi.json honest — a guarded route would
    otherwise be advertised as open — and gives Swagger UI its "Authorize" box.

    Returns:
        The configuration when docs are enabled, otherwise None.
    """
    if not docs_enabled:
        return None

    return OpenAPIConfig(
        title="Dashboard Discord API",
        version="1.0.0",
        components=Components(
            security_schemes={
                "APIKey": SecurityScheme(
                    type="apiKey",
                    name=API_KEY_HEADER,
                    security_scheme_in="header",
                    description="Injected server-side by nginx for the frontend; supplied directly by third-party clients.",
                )
            }
        ),
    )


def build_rate_limit_config(
    rate_limit: tuple[DurationUnit, int] = ("minute", 120),
) -> RateLimitConfig:
    """Return the rate limit configuration, counted per client address.

    The API sits on a public domain and the access logs show it being swept
    continuously, so an unbounded endpoint is an open invitation.

    Two limits worth knowing. The counter lives in the default in-memory store, so
    each Granian worker keeps its own: with WEB_CONCURRENCY=4 the effective quota is
    four times this value. Making it exact — and surviving several API replicas —
    needs a shared store such as Redis. And /api/health is exempt, since the compose
    healthcheck calls it every 10 seconds and must never be throttled.

    Args:
        rate_limit: Duration unit and number of requests allowed per client.

    Returns:
        The configured rate limit.
    """
    return RateLimitConfig(
        rate_limit=rate_limit,
        exclude=["/api/health"],
        identifier_for_request=identify_client,
    )


rate_limit_config = build_rate_limit_config()

app = Litestar(
    plugins=plugins,
    route_handlers=[api_router],
    middleware=[rate_limit_config.middleware],
    exception_handlers={AppError: app_error_handler},
    openapi_config=build_openapi_config(docs_enabled=DOCS_ENABLED),
    # Checked at startup, not at import: the CLI (`litestar assets generate-types`)
    # loads this module without needing a key, nor a database.
    on_startup=[ensure_api_key_configured, ensure_database_configured],
    # One engine for the process, disposed on shutdown.
    lifespan=[db_lifespan],
)
