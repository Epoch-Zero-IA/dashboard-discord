#!/bin/sh
# Migrations run here, once, before the application starts.
#
# Why this container and not the worker's: a single migrator means no coordination and
# no race. `bot` waits for `api` to be healthy, so it only ever starts against an
# up-to-date schema.
#
# Why the entrypoint and not a one-shot compose service: Coolify restarts one-shot
# services badly, and this runs before Granian forks its workers — so once per
# container, not once per WEB_CONCURRENCY.
#
# `set -e` matters: a failed migration must stop the container rather than start an API
# against a schema it does not match. The container then never becomes healthy, so
# `depends_on: service_healthy` keeps web and bot down and the deployment reports it.
set -e

echo "Running database migrations..."
alembic upgrade head

exec "$@"
