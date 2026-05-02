# Architecture

## Overview

Network Supervisor is a backend-first system designed to monitor and automate network equipment management, specifically targeting UISP/Ubiquiti devices.

```
┌─────────────┐     ┌──────────────────────────────────┐     ┌──────────┐
│   Client     │────▶│         FastAPI Backend           │────▶│ PostgreSQL│
│ (curl/UI)    │◀────│                                  │◀────│          │
└─────────────┘     │  ┌──────────┐  ┌──────────────┐  │     └──────────┘
                    │  │ API Layer│  │  Scheduler    │  │
                    │  │ /api/v1/ │  │ (APScheduler) │  │
                    │  └──────────┘  └──────────────┘  │
                    │  ┌──────────┐  ┌──────────────┐  │
                    │  │ Services │  │    Tasks      │  │
                    │  └──────────┘  └──────────────┘  │
                    │  ┌─────────────────────────────┐  │
                    │  │    Models / SQLAlchemy       │  │
                    │  └─────────────────────────────┘  │
                    └──────────────────────────────────┘
```

## Directory Structure

```
backend/app/
├── main.py          # App factory + lifespan
├── core/            # Config, logging, exceptions
├── api/endpoints/   # REST endpoints (health, devices)
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response models
├── services/        # Business logic layer
├── tasks/           # APScheduler jobs
├── db/              # Database engine & session
└── utils/           # Shared helpers
```

## Data Model

| Table | Purpose |
|---|---|
| `devices` | Network equipment inventory |
| `device_metrics` | Time-series metrics from devices |
| `incidents` | Detected problems/anomalies |
| `alerts` | Notifications sent for incidents |
| `power_status_logs` | UISP Power readings |
| `notification_channels` | Alert delivery channels (email, webhook, SMS) |

## Key Patterns

- **Async everywhere**: FastAPI + async SQLAlchemy + asyncpg
- **Dependency injection**: FastAPI `Depends()` for DB sessions
- **Service layer**: Business logic separated from API handlers
- **Pydantic validation**: All API I/O validated via schemas
- **Scheduler lifecycle**: Tied to FastAPI lifespan (start on boot, stop on shutdown)
- **Alembic migrations**: Async-compatible, auto-detects model changes
