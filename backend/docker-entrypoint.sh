#!/usr/bin/env bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting application (APP_ENV=${APP_ENV:-development})..."

if [ "${APP_ENV:-development}" = "production" ]; then
    # Production: no reload, single worker (APScheduler must run in one process only)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
else
    # Development: hot reload enabled
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
fi
