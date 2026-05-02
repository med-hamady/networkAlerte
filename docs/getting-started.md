# Getting Started

## Prerequisites

- **Docker** and **Docker Compose** installed
- **Git** installed

## Quick Start

### 1. Clone and enter the project

```bash
cd a2project
```

### 2. Create environment file

```bash
cp .env.example .env
```

Edit `.env` if you need to change database credentials or other settings.

### 3. Start all services

```bash
docker compose up --build
```

This starts:
- **PostgreSQL 16** on port `5432`
- **FastAPI backend** on port `8000`

Database migrations run automatically on backend startup.

### 4. Verify it works

```bash
# Health check
curl http://localhost:8000/api/v1/health

# API docs (Swagger)
# Open http://localhost:8000/docs in your browser

# List devices (should return empty list)
curl http://localhost:8000/api/v1/devices
```

### 5. Generate a new migration (after model changes)

```bash
docker compose exec backend alembic revision --autogenerate -m "description of changes"
```

To run migrations manually (e.g. after a `docker compose down -v`):

```bash
docker compose exec backend alembic upgrade head
```

## Stopping

```bash
docker compose down          # stop containers
docker compose down -v       # stop and remove volumes (destroys DB data)
```

## Development Tips

- Backend hot-reloads automatically (volume mount + uvicorn --reload)
- API docs at http://localhost:8000/docs
- ReDoc at http://localhost:8000/redoc
- Logs: `docker compose logs -f backend`
