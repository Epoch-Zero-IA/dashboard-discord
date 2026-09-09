# Task runner for dashboard-discord — run `just` to list recipes.
# Recipes mirror the pre-commit hooks and are the single source the CI calls.
# Install just: `uv tool install rust-just` (or your package manager).

# Load .env for every recipe: the API key must reach both the Litestar guard and the
# Vite dev proxy, and Vite only exposes VITE_-prefixed variables on its own.
set dotenv-load := true

# List available recipes.
default:
    @just --list

# Install backend deps + the frontend node_modules.
install:
    uv sync
    uv run litestar assets install

# Vite proxies /api to the API, so browse the app on :5173.

# Run API (:8000) and frontend (:5173) together; Ctrl-C stops both.
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    just dev-api &
    just dev-front &
    wait

# Watch backend/ only: Vite writes frontend/dist/hot on startup, which would
# otherwise restart the API every time the frontend boots.

# API only, with reload. Serves no frontend: `/` returns 404 by design.
dev-api:
    uv run litestar run --reload --reload-dir backend

# Frontend only, with HMR. Proxies /api to the API on :8000.
dev-front:
    pnpm -C frontend dev

# Run after touching a route or a response type, then commit openapi.json.

# ENABLE_DOCS is forced on: without openapi_config the schema leaves memory and the
# command exports nothing — silently, which would let the contract drift unnoticed.

# Export openapi.json from the handlers and derive the TypeScript client.
types:
    ENABLE_DOCS=true uv run litestar assets generate-types

# Static checks, no writes: ruff + pyrefly (Python), eslint + prettier + svelte-check (frontend).
lint:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyrefly check
    pnpm -C frontend run lint
    # svelte-check resolves vite.config.ts to get the preprocessor, and the litestar
    # plugin refuses to configure a dev server when CI=true. Nothing is served here —
    # only the config is read — so the check is bypassed rather than worked around.
    LITESTAR_BYPASS_ENV_CHECK=1 pnpm -C frontend exec svelte-check

# Apply formatting and safe fixes: ruff (Python), prettier + eslint (frontend).
format:
    uv run ruff format .
    uv run ruff check --fix .
    pnpm -C frontend run format

# With no database reachable the `db`-marked tests skip, with a message naming
# `just db`, and the rest of the suite still runs.

# Run the test suite with coverage.
test:
    uv run pytest

# What `just check` and the CI run: the gate before a push must not go green having
# quietly run half of the suite. Needs `just db` locally; CI provides the service.

# The same suite, an unreachable database being an error rather than a skip.
test-with-db:
    uv run pytest --require-db

# Build the production frontend bundle into frontend/dist (no Python needed).
build:
    pnpm -C frontend build

# The guard that replaces the build-time coupling between frontend and backend.

# Fail if openapi.json drifted from the handlers.
check-types: types
    git diff --exit-code openapi.json

# Full gate before pushing (what CI runs): contract check + lint + tests, database included.
check: check-types lint test-with-db

# The tests, alembic and `just dev-bot` all run on the host and reach it over the
# published port, so this is the one service worth starting by itself.

# Start the Postgres service alone.
db:
    docker compose up -d db

# Apply every pending migration to the database DATABASE_URL points at.
migrate:
    uv run alembic upgrade head

# Autogenerate diffs against a live, already-migrated database — not against the
# revision history — so `just db && just migrate` come first. Read the generated file
# before committing it: autogenerate does not guess partial indexes.

# Write a migration from the gap between the models and the database.
migration m:
    uv run alembic revision --autogenerate -m "{{m}}"

# Needs DISCORD_TOKEN and both privileged intents enabled in the developer portal —
# the worker refuses to start otherwise, on purpose.

# The Discord worker alone, against the local database.
dev-bot:
    uv run python -m bot

# `just dev` deliberately leaves the worker out, so that working on the frontend needs
# no token and opens no real gateway connection.

# The whole stack: API, frontend and worker.
dev-all:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    just dev-api &
    just dev-front &
    just dev-bot &
    wait

# The catch-up on startup is budgeted so it does not hold the connection for an hour;
# this is the same machinery with the budget removed. Resumable: it picks up at the
# cursors, so an interrupted run costs at most one page.

# Import the history of every readable channel, to completion, then exit.
backfill:
    uv run python -m bot backfill
