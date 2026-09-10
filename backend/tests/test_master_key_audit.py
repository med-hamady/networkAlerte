"""L'usage de la clé MAÎTRESSE doit laisser une trace — sinon on ne peut pas la tourner.

Contexte : la rotation d'`API_KEY` est décidée depuis le 2026-08-11 (l'équipe
paiement la détenait) mais restait inexécutable, faute de savoir QUI en dépend
encore. Le `log_format main` de nginx n'enregistre aucun en-tête : un appel porté
par la clé maîtresse et un appel porté par une clé cloisonnée y sont identiques.
Tourner à l'aveugle, c'est casser un consommateur inconnu en production.

Ce que ces tests verrouillent :

  - la clé maîtresse journalise son usage, avec le CHEMIN (le seul renseignement
    qui dise quel consommateur dépend de quoi) ;
  - le secret lui-même n'atterrit JAMAIS dans le log — un audit qui recopie la
    clé dans un fichier rotatif remplace un problème par un pire ;
  - une clé CLOISONNÉE ne déclenche pas cette trace : sinon le signal se noierait
    dans les appels légitimes des tiers déjà migrés, et l'audit ne servirait plus
    à rien.
"""

import logging

import pytest
from starlette.requests import Request

from app.api import deps

MASTER = "master-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SIGNAL = "signal-key-ffffffffffffffffffffffffffffffff"


@pytest.fixture
def keys(monkeypatch, settings):
    monkeypatch.setattr(settings, "api_key", MASTER, raising=False)
    monkeypatch.setattr(settings, "client_signal_api_key", SIGNAL, raising=False)
    return settings


def _request(path: str = "/api/v1/devices", method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"mac=aa:bb:cc:dd:ee:ff",
            "headers": [(b"host", b"10.135.3.25"), (b"x-forwarded-for", b"102.0.0.9")],
            "client": ("172.18.0.5", 51234),
            "scheme": "https",
            "server": ("10.135.3.25", 443),
        },
    )


@pytest.mark.asyncio
async def test_master_key_use_is_logged_with_its_path(keys, caplog):
    """La clé maîtresse laisse une ligne WARNING nommant méthode, chemin et source."""
    with caplog.at_level(logging.WARNING, logger=deps.logger.name):
        user = await deps.require_user_or_api_key(_request(), MASTER, db=None)

    assert user is None  # authentifié par clé, aucune identité utilisateur
    records = [r for r in caplog.records if "master API_KEY" in r.getMessage()]
    assert len(records) == 1, "un usage de la clé maîtresse doit produire une ligne"

    line = records[0].getMessage()
    assert "/api/v1/devices" in line
    assert "GET" in line
    assert "102.0.0.9" in line  # l'IP réelle de l'appelant, via X-Forwarded-For


@pytest.mark.asyncio
async def test_the_secret_itself_never_reaches_the_log(keys, caplog):
    """On journalise l'USAGE, jamais la clé — ni la query string qui la borde.

    Un audit qui recopie le secret dans un fichier de log rotatif (monté sur
    disque, lu par l'exploitation) aggrave le problème qu'il documente. Idem pour
    la query string, qui porte des identifiants d'abonné (`?mac=`).
    """
    with caplog.at_level(logging.WARNING, logger=deps.logger.name):
        await deps.require_user_or_api_key(_request(), MASTER, db=None)

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert MASTER not in blob
    assert "aa:bb:cc:dd:ee:ff" not in blob


@pytest.mark.asyncio
async def test_a_scoped_key_does_not_trigger_the_master_trace(keys, caplog):
    """Un tiers déjà migré ne pollue pas l'audit — sinon le signal est illisible."""
    with caplog.at_level(logging.WARNING, logger=deps.logger.name):
        user = await deps.require_client_signal_client(
            _request("/api/v1/client-signal"), SIGNAL, db=None,
        )

    assert user is None
    assert not [r for r in caplog.records if "master API_KEY" in r.getMessage()]
