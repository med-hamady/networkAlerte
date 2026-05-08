# =============================================================================
# Network Supervisor — Makefile
# =============================================================================
# Usage:
#   make dev          Démarre l'environnement de développement (hot-reload)
#   make prod         Démarre la production (build + nginx)
#   make logs         Suit tous les logs en temps réel
#   make ps           État des containers
#   make stop         Arrête les containers (sans supprimer)
#   make down         Arrête et supprime les containers dev
#   make shell        Shell bash dans le backend
#   make db           Shell psql dans la DB
#   make migrate      Applique les migrations Alembic
#   make lint         Lance Ruff sur le backend

.PHONY: dev dev-build prod prod-restart prod-stop prod-down \
        logs logs-backend logs-frontend logs-nginx \
        ps stop down shell db migrate lint

COMPOSE_BASE := docker compose -f docker-compose.yml
COMPOSE_PROD := $(COMPOSE_BASE) -f docker-compose.prod.yml

# ── Développement ────────────────────────────────────────────────────────────

dev:
	$(COMPOSE_BASE) up -d

dev-build:
	$(COMPOSE_BASE) up -d --build

dev-down:
	$(COMPOSE_BASE) down

# ── Production ───────────────────────────────────────────────────────────────

prod:
	$(COMPOSE_PROD) up -d --build

prod-restart:
	$(COMPOSE_PROD) restart

prod-stop:
	$(COMPOSE_PROD) stop

prod-down:
	$(COMPOSE_PROD) down

# ── Logs ─────────────────────────────────────────────────────────────────────

logs:
	docker compose logs -f --tail=100

logs-backend:
	docker compose logs -f --tail=100 backend

logs-frontend:
	docker compose logs -f --tail=100 frontend

logs-nginx:
	docker compose logs -f --tail=100 nginx

# ── Inspection ───────────────────────────────────────────────────────────────

ps:
	docker compose ps

stop:
	docker compose stop

down:
	docker compose down

# ── Outils backend ───────────────────────────────────────────────────────────

shell:
	docker compose exec backend bash

db:
	docker compose exec postgres psql -U $${POSTGRES_USER:-supervisor} -d $${POSTGRES_DB:-network_supervisor}

migrate:
	docker compose exec backend alembic upgrade head

lint:
	docker compose exec backend ruff check app/
	docker compose exec backend ruff format app/ --check
