"""
Reprise de l'AP/IP depuis UISP quand le radio ne voit plus la station.

Le rattachement radio (`discovery_service`) lit la liste des stations d'un AP :
il ne peut donc corriger qu'un client **allumé**. Un client qui déménage vers un
autre AP PUIS tombe en panne n'était corrigé par personne — sa ligne restait
figée sur son ancien AP, son ancien site et son ancienne IP (morte, donc plus
pingeable, donc « hors ligne » indéfiniment), alors que la colonne
`uisp_ap_name` de sa propre ligne portait déjà la bonne réponse.

Constaté le 2026-07-22 : LR 598, servi par A2-DN1-SUD1 depuis le 27/06, affiché
sur A2 AT1 avec `10.135.5.152`. UISP : « Device Is In Outage, last seen 1h »,
AP = A2-DN1-SUD1 — l'AP ne le liste pas (il est down), UISP si.

RÈGLE ARBITRÉE ICI : **la source qui l'a vu le plus récemment gagne.** Tant que
le radio le voit (poll 60 s) il reste propriétaire ; dès qu'il le perd, UISP
prend le relais. Sans arbitrage, deux écrivains sur `rocket_id` le feraient
osciller à chaque cycle.
"""

import datetime

import pytest

from app.models.device import Lr, Rocket
from app.services.uisp_sync_service import _adopt_uisp_attribution, _norm_name


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@pytest.fixture
def rockets():
    old = Rocket(name="A2-AT1-SUD1", location="A2 AT1", radio_tech="airmax")
    new = Rocket(name=" A2-DN1-SUD1 ", location="A2 DN1", radio_tech="airmax")
    old.id, new.id = 855, 1794
    return {_norm_name(old.name): old, _norm_name(new.name): new}


def _lr(**kw) -> Lr:
    lr = Lr(name="32469697-Yakoub", model_variant="litebeam_5ac", **kw)
    lr.id = 598
    return lr


class _FakeSession:
    """`release_ip_if_held` n'est pas testé ici — il a sa couverture ailleurs."""

    async def execute(self, *a, **k):
        raise AssertionError("aucune requête ne doit partir dans ces cas")


async def test_uisp_reparents_when_radio_has_not_seen_it_since(rockets, monkeypatch):
    """Le cas fondateur : radio muet depuis 3 semaines, UISP l'a vu il y a 1 h."""
    lr = _lr(rocket_id=855, location="A2 AT1", ip_address="10.135.5.152",
             last_discovered_at=_now() - datetime.timedelta(days=25))
    summary = {"reparented": 0, "ip_updated": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", None,
        _now() - datetime.timedelta(hours=1), rockets, summary,
    )
    assert lr.rocket_id == 1794
    assert lr.location == "A2 DN1"   # le site suit l'AP
    assert summary["reparented"] == 1


async def test_radio_wins_while_it_still_sees_the_station(rockets):
    """Client allumé : le radio (poll 60 s) reste propriétaire, UISP ne touche rien.

    C'est ce qui empêche l'oscillation entre deux écrivains sur `rocket_id`.
    """
    lr = _lr(rocket_id=855, location="A2 AT1",
             last_discovered_at=_now() - datetime.timedelta(seconds=30))
    summary = {"reparented": 0, "ip_updated": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", "10.135.4.13",
        _now() - datetime.timedelta(hours=1), rockets, summary,
    )
    assert lr.rocket_id == 855
    assert summary == {"reparented": 0, "ip_updated": 0}


async def test_uisp_without_a_timestamp_never_wins(rockets):
    """Pas de `lastSeen` = aucune preuve de fraîcheur → on ne touche à rien."""
    lr = _lr(rocket_id=855, last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", None, None, rockets, summary,
    )
    assert lr.rocket_id == 855


async def test_ap_name_is_matched_despite_spaces_and_case(rockets):
    """Les noms UISP arrivent sales (` A2-HQ-SUD `) — un match strict perdrait le client."""
    lr = _lr(rocket_id=855, last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "  a2-dn1-sud1  ", None,
        _now(), rockets, summary,
    )
    assert lr.rocket_id == 1794


async def test_unknown_ap_leaves_the_row_alone(rockets):
    """AP absent de notre inventaire → aucun rattachement inventé."""
    lr = _lr(rocket_id=855, last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-AILLEURS-NORD", None, _now(), rockets, summary,
    )
    assert lr.rocket_id == 855
    assert summary["reparented"] == 0


async def test_ip_outside_the_management_plan_is_refused(rockets):
    """UISP remonte aussi des LAN de CPE — même garde-fou que la découverte.

    `_FakeSession.execute` lèverait si on tentait de libérer l'IP : la preuve
    que le filtre coupe AVANT toute écriture.
    """
    lr = _lr(rocket_id=1794, ip_address="10.135.4.13", last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", "192.168.10.1", _now(), rockets, summary,
    )
    assert lr.ip_address == "10.135.4.13"
    assert summary["ip_updated"] == 0


# ── Test d'intégration : le résumé réel doit porter les compteurs ────────────
# Régression vécue : les clés `reparented`/`ip_updated` avaient été insérées
# dans le résumé du sync INFRA au lieu de celui des STATIONS. Les tests
# unitaires ci-dessus passaient (ils fabriquent leur propre dict), mais le vrai
# sync levait un KeyError au premier client rerattaché. Seul un passage par la
# vraie fonction pouvait l'attraper.

class _FakeUISPClient:
    """Contrôleur UISP simulé : une station, vue il y a 1 h, sur le nouvel AP."""

    def __init__(self, *a, **kw):
        pass

    async def fetch_devices(self, role=None):
        return [{
            "identification": {
                "id": "sta-1", "mac": "1C:6A:1B:B8:79:B0",
                "name": "32469697-Yakoub", "modelName": "LiteBeam 5AC",
            },
            "overview": {
                "status": "disconnected",   # DOWN : l'AP ne le liste pas
                "lastSeen": (
                    datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
                ).isoformat(),
                "wirelessMode": "sta-ptmp",
            },
            "mode": "router",
            "ipAddress": "10.135.4.13/16",
            "attributes": {"apDevice": {"name": "A2-DN1-SUD1"}},
        }]

    async def fetch_data_links(self):
        return []


async def test_station_sync_reparents_a_down_client_end_to_end(db, monkeypatch):
    from app.services import uisp_sync_service

    old_ap = Rocket(name="ZZ-AT1-SUD1", location="ZZ AT1", radio_tech="airmax",
                    ip_address="10.99.200.1", status="up")
    new_ap = Rocket(name="A2-DN1-SUD1", location="A2 DN1", radio_tech="airmax",
                    ip_address="10.99.200.2", status="up")
    db.add_all([old_ap, new_ap])
    await db.flush()

    lr = Lr(
        name="32469697-Yakoub", model_variant="litebeam_5ac", status="down",
        ip_address="10.135.5.152", mac_address="1c:6a:1b:b8:79:b0",
        rocket_id=old_ap.id, location="ZZ AT1", auto_discovered=True,
        # Le radio ne l'a pas vu depuis 25 jours → UISP (1 h) doit gagner.
        last_discovered_at=_now() - datetime.timedelta(days=25),
    )
    db.add(lr)
    await db.flush()

    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _FakeUISPClient)
    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["reparented"] == 1, "le résumé des STATIONS doit porter le compteur"
    assert summary["ip_updated"] == 1
    # ⚠️ `refresh()` ne flushe PAS les modifications en attente : il expire
    # l'objet et le relit, donc il ÉCRASE silencieusement ce qui n'est pas
    # encore écrit. Sans ce flush, le test relisait l'ancienne IP et accusait
    # le code à tort (`rocket_id`, lui, avait survécu parce qu'un autoflush
    # interne l'avait déjà poussé). On flushe donc pour prouver que la valeur
    # atteint vraiment la base, puis on relit.
    await db.flush()
    await db.refresh(lr)
    assert lr.rocket_id == new_ap.id
    assert lr.location == "A2 DN1"
    assert lr.ip_address == "10.135.4.13"
