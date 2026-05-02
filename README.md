# Network Supervisor

> Supervision and automation system for UISP/Ubiquiti network equipment.

## Quick Start

```bash
# 1. Create environment file
cp .env.example .env

# 2. Start everything
docker compose up --build

# 3. Verify
curl http://localhost:8000/api/v1/health
```

**API docs**: http://localhost:8000/docs

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy (async) |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Scheduler | APScheduler |
| Network | pysnmp, paramiko, httpx |
| Infra | Docker Compose |
| Quality | Ruff, pre-commit |

## Project Structure

```
backend/app/
├── main.py          # App entry point
├── core/            # Config, logging, exceptions
├── api/endpoints/   # REST API (health, devices)
├── models/          # Database models (6 tables)
├── schemas/         # Pydantic validation
├── services/        # Business logic
├── tasks/           # Scheduled jobs
├── db/              # Database connection
└── utils/           # Helpers
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/devices` | List devices |
| POST | `/api/v1/devices` | Create device |
| GET | `/api/v1/devices/{id}` | Get device |
| PUT | `/api/v1/devices/{id}` | Update device |
| DELETE | `/api/v1/devices/{id}` | Delete device |

## Database Migrations

Migrations run automatically on backend startup.

```bash
# Create new migration after model changes
docker compose exec backend alembic revision --autogenerate -m "description"

# Run migrations manually if needed
docker compose exec backend alembic upgrade head
```

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Next Steps](docs/next-steps.md)

## Target Equipment

- LTU Rocket / Rocket airMax
- LTU LR / LiteBeam
- UISP Switch
- UISP Power
