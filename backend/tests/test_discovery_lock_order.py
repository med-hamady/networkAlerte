"""Ordre de verrouillage de la découverte — `_peers_in_lock_order`.

Ce que ce test verrouille, et la panne qui l'a motivé
----------------------------------------------------
Les logs Postgres du 2026-08-11 nomment TOUJOURS la même paire d'instructions,
dans les deux sens, pour 87 interblocages en 24 h :

    Process A: UPDATE devices SET last_seen=…          WHERE devices.id = $3
    Process B: UPDATE devices SET last_discovered_at=… WHERE devices.id = $3

`last_seen` est le sweep de ping ; `last_discovered_at` est la découverte. Deux
transactions qui écrivent beaucoup de lignes `devices` une par une — l'une par id
croissant (corrigé d'abord), l'autre dans l'ordre où la RADIO a annoncé ses
stations, qui change à chaque tour. Ordonner un seul des deux côtés ne suffit
pas : il faut que TOUT LE MONDE prenne ses verrous dans le même sens.
"""

import pytest

from app.services.discovery_service import _peers_in_lock_order


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Session minimale : rend le couple (id, mac) comme le vrai SELECT."""

    def __init__(self, id_by_mac):
        self._id_by_mac = id_by_mac
        self.executed = 0

    async def execute(self, _query):
        self.executed += 1
        return _FakeResult([(v, k) for k, v in self._id_by_mac.items()])


def _peer(mac):
    """Un `PeerInfo` est un DICT (`peer.get("mac")`), pas un objet — un MagicMock
    y rendait un mock pour `.get("mac")` et faisait échouer la normalisation."""
    return {"mac": mac}


@pytest.mark.asyncio
async def test_existing_devices_are_walked_by_ascending_id():
    """Le cœur du correctif : l'ordre d'annonce de la radio ne doit plus dicter
    l'ordre de verrouillage."""
    peers = [_peer("aa:00:00:00:00:01"), _peer("bb:00:00:00:00:02"), _peer("cc:00:00:00:00:03")]
    session = _FakeSession({
        "aa:00:00:00:00:01": 900,
        "bb:00:00:00:00:02": 705,
        "cc:00:00:00:00:03": 1013,
    })

    ordered = await _peers_in_lock_order(session, peers)

    assert [p["mac"] for _, p in ordered] == [
        "bb:00:00:00:00:02",  # id 705
        "aa:00:00:00:00:01",  # id 900
        "cc:00:00:00:00:03",  # id 1013
    ]


@pytest.mark.asyncio
async def test_the_original_index_is_preserved():
    """`fallback_index` NOMME les peers sans MAC. Le renuméroter selon le nouvel
    ordre renommerait des CPE à chaque cycle, au gré de la radio."""
    peers = [_peer("aa:00:00:00:00:01"), _peer("bb:00:00:00:00:02")]
    session = _FakeSession({"aa:00:00:00:00:01": 900, "bb:00:00:00:00:02": 705})

    ordered = await _peers_in_lock_order(session, peers)

    # Le peer réordonné en tête garde son index d'origine (2), pas 1.
    assert ordered[0][0] == 2
    assert ordered[1][0] == 1


@pytest.mark.asyncio
async def test_unknown_macs_are_created_last():
    """Une MAC inconnue est une CRÉATION : sa ligne n'existe pas encore, donc elle
    ne peut entrer en conflit avec personne. Elle passe en fin de liste plutôt que
    de s'intercaler entre deux verrous existants."""
    peers = [_peer("ff:00:00:00:00:09"), _peer("aa:00:00:00:00:01")]
    session = _FakeSession({"aa:00:00:00:00:01": 900})

    ordered = await _peers_in_lock_order(session, peers)

    assert [p["mac"] for _, p in ordered] == ["aa:00:00:00:00:01", "ff:00:00:00:00:09"]


@pytest.mark.asyncio
async def test_no_mac_at_all_costs_no_query():
    """Aucune MAC à résoudre ⇒ aucun SELECT : la résolution ne doit pas ajouter un
    aller-retour à chaque Rocket dont les peers sont anonymes."""
    peers = [_peer(None), _peer(None)]
    session = _FakeSession({})

    ordered = await _peers_in_lock_order(session, peers)

    assert session.executed == 0
    assert [i for i, _ in ordered] == [1, 2]


@pytest.mark.asyncio
async def test_empty_peer_list_is_untouched():
    session = _FakeSession({})
    assert await _peers_in_lock_order(session, []) == []
