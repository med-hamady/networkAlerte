"""Un job est rejoué UNE fois quand l'ouverture de la connexion à la base échoue.

Mesuré en prod le 2026-09-26 : power_poll_job a planté 3 fois en 12 h sur
`socket.gaierror: Temporary failure in name resolution` — le DNS interne de
Docker n'a pas résolu `postgres` à cet instant ; Postgres n'a jamais vu la
tentative. Le rejeu ne doit porter QUE sur ce cas : un OSError levé ailleurs
(SSH, réseau vers un équipement) doit remonter comme avant.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from app.tasks import jobs

# Une frame dont le fichier ressemble au pool SQLAlchemy : c'est ce que
# `_db_connect_failure` cherche dans la pile.
_POOL_NS: dict = {}
exec(  # noqa: S102 — fabrique une frame « sqlalchemy/pool/base.py »
    compile(
        "def pool_connect():\n"
        "    raise socket.gaierror(-3, 'Temporary failure in name resolution')\n",
        "/usr/local/lib/python3.12/site-packages/sqlalchemy/pool/base.py",
        "exec",
    ),
    {"socket": socket},
    _POOL_NS,
)
_pool_connect = _POOL_NS["pool_connect"]


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(jobs, "_JOB_DB_CONNECT_RETRY_S", 0)


def _job(failures: int, raiser):
    calls = {"n": 0}

    @jobs._timed_job
    async def fake_job():
        calls["n"] += 1
        if calls["n"] <= failures:
            raiser()
        return "ok"

    return fake_job, calls


def test_dns_failure_at_connect_is_replayed_once():
    job, calls = _job(1, _pool_connect)
    assert asyncio.run(job()) == "ok"
    assert calls["n"] == 2


def test_a_persistent_connect_failure_still_surfaces():
    """Un seul rejeu : un DNS durablement muet est une vraie panne."""
    job, calls = _job(5, _pool_connect)
    with pytest.raises(socket.gaierror):
        asyncio.run(job())
    assert calls["n"] == 2


def test_an_oserror_outside_the_db_connect_is_not_replayed():
    """Une erreur réseau vers un équipement ne rejoue pas le job entier."""
    def _ssh_down():
        raise ConnectionResetError("LR a coupé la session")

    job, calls = _job(1, _ssh_down)
    with pytest.raises(ConnectionResetError):
        asyncio.run(job())
    assert calls["n"] == 1


def test_detection_requires_a_db_connect_frame():
    try:
        _pool_connect()
    except socket.gaierror as exc:
        assert jobs._db_connect_failure(exc)
    assert not jobs._db_connect_failure(socket.gaierror(-3, "hors pile"))
    assert not jobs._db_connect_failure(ValueError("pas une erreur réseau"))
