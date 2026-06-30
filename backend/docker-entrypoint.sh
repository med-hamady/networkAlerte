#!/usr/bin/env bash
set -e

# RUN_MODE=scheduler  → standalone scheduler process (no HTTP server, no migrations).
#                       Used in production to isolate APScheduler from uvicorn workers.
#                       Migrations are owned by the API container — this one just waits
#                       for it to be healthy via depends_on.
# RUN_MODE=collector  → standalone NetFlow collector (UDP listener + flush, no HTTP
#                       server, no migrations). Same isolation rationale as scheduler.
# RUN_MODE=api (default) → run migrations + uvicorn (existing behaviour).

RUN_MODE="${RUN_MODE:-api}"

if [ "$RUN_MODE" = "scheduler" ]; then
    echo "Starting scheduler runner (APP_ENV=${APP_ENV:-development})..."
    exec python -m app.tasks.runner
fi

if [ "$RUN_MODE" = "collector" ]; then
    echo "Starting NetFlow collector runner (APP_ENV=${APP_ENV:-development})..."
    exec python -m app.tasks.collector_runner
fi

echo "Running database migrations..."
alembic upgrade head

echo "Starting API (APP_ENV=${APP_ENV:-development})..."

if [ "${APP_ENV:-development}" = "production" ]; then
    # Production: no reload. UVICORN_WORKERS configurable — safe to raise above 1
    # ONLY when the scheduler runs in its own container (SCHEDULER_ENABLED=false here).
    # See docker-compose.prod.yml.
    # --proxy-headers + --forwarded-allow-ips='*' makes FastAPI trust X-Forwarded-Proto
    # from the reverse proxy so HTTPS redirects (trailing slash etc.) keep the https scheme.
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        --workers "${UVICORN_WORKERS:-1}" \
        --proxy-headers --forwarded-allow-ips='*'
else
    # Development: hot reload enabled, scheduler runs in-process (SCHEDULER_ENABLED=true)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
fi
