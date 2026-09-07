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

# Run the test suite with coverage.
test:
    uv run pytest

# Build the production frontend bundle into frontend/dist (no Python needed).
build:
    pnpm -C frontend build

# The guard that replaces the build-time coupling between frontend and backend.

# Fail if openapi.json drifted from the handlers.
check-types: types
    git diff --exit-code openapi.json

# Full gate before pushing (what CI runs): contract check + lint + tests.
check: check-types lint test
