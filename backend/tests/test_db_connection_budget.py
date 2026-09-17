"""
Budget de connexions Postgres de la prod — `docker-compose.prod.yml`.

Chaque process Python (API, schedulers, collecteur) ouvre son PROPRE réservoir
SQLAlchemy, jusqu'à `DB_POOL_SIZE + DB_MAX_OVERFLOW` connexions, et l'API en ouvre
un par worker uvicorn. La somme doit rester sous `max_connections`, sinon Postgres
refuse les nouvelles connexions (« too many clients ») — et ce sont alors des
jobs entiers qui échouent sans bruit.

Le 2026-09-17, on a doublé les workers de l'API et la parallélisation des polls
LTU/airOS pour utiliser les ressources du serveur. Ce test garantit qu'un réglage
de ce genre ne peut pas être relevé seul : si le total dépasse le plafond, il
échoue avant le déploiement.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD = REPO_ROOT / "docker-compose.prod.yml"

DEFAULT_POOL = 5          # Settings.db_pool_size
DEFAULT_OVERFLOW = 10     # Settings.db_max_overflow
DEFAULT_PERSIST = 8       # Settings.poll_persist_concurrency
SUPERUSER_RESERVED = 3    # superuser_reserved_connections de Postgres
TOOLING_MARGIN = 10       # psql, scripts d'exploitation, alembic à la main

APP_PROCESSES = ("backend", "netflow-collector")


class _Loader(yaml.SafeLoader):
    """Tolère les balises propres à Compose (`!reset`)."""


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _prod() -> dict:
    if not PROD.exists():
        pytest.skip("docker-compose.prod.yml hors de portée (tests lancés dans le conteneur)")
    return yaml.load(PROD.read_text(encoding="utf-8"), Loader=_Loader)


def _env(service: dict) -> dict:
    env = service.get("environment") or {}
    return {k: str(v) for k, v in env.items()} if isinstance(env, dict) else {}


def _int(value: str | None, default: int) -> int:
    if value is None:
        return default
    m = re.search(r"\$\{[A-Z_]+:-(\d+)\}", value)  # "${UVICORN_WORKERS:-4}"
    return int(m.group(1) if m else value)


def _app_services(compose: dict) -> dict:
    return {
        name: svc for name, svc in compose["services"].items()
        if name in APP_PROCESSES or name.startswith("scheduler")
    }


def _pool_capacity(env: dict) -> int:
    return _int(env.get("DB_POOL_SIZE"), DEFAULT_POOL) + _int(env.get("DB_MAX_OVERFLOW"), DEFAULT_OVERFLOW)


def _max_connections(compose: dict) -> int:
    command = compose["services"]["postgres"].get("command") or []
    for arg in command:
        if str(arg).startswith("max_connections="):
            return int(str(arg).split("=", 1)[1])
    pytest.fail("max_connections n'est pas fixé dans la commande postgres de la prod")


def test_le_total_des_reservoirs_tient_sous_max_connections():
    compose = _prod()
    total = 0
    detail = []
    for name, svc in _app_services(compose).items():
        env = _env(svc)
        processes = _int(env.get("UVICORN_WORKERS"), 1) if name == "backend" else 1
        worst = processes * _pool_capacity(env)
        detail.append(f"{name}={worst}")
        total += worst

    ceiling = _max_connections(compose) - SUPERUSER_RESERVED - TOOLING_MARGIN
    assert total <= ceiling, f"{total} connexions possibles > {ceiling} : " + ", ".join(detail)


def test_la_parallelisation_des_polls_tient_dans_leur_reservoir():
    """Une tâche de phase 2 = une connexion : sinon elle attend le réservoir.

    Seuls les polls LTU et airOS lisent `POLL_PERSIST_CONCURRENCY` : les autres
    conteneurs peuvent garder un petit réservoir.
    """
    checked = []
    for name, svc in _app_services(_prod()).items():
        env = _env(svc)
        if env.get("SCHEDULER_GROUP") not in ("poll-ltu", "poll-airos"):
            continue
        checked.append(name)
        persist = _int(env.get("POLL_PERSIST_CONCURRENCY"), DEFAULT_PERSIST)
        # +2 : la session de lecture des cibles et celle des seuils.
        assert persist + 2 <= _pool_capacity(env), name
    assert sorted(checked) == ["scheduler-poll-airos", "scheduler-poll-ltu"]
