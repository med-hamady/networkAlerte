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
    summary = {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", None, "active",
        _now() - datetime.timedelta(hours=1), rockets, set(), summary,
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
    summary = {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", "10.135.4.13", "active",
        _now() - datetime.timedelta(hours=1), rockets, set(), summary,
    )
    assert lr.rocket_id == 855
    assert summary == {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}


async def test_uisp_without_a_timestamp_never_wins(rockets):
    """Pas de `lastSeen` = aucune preuve de fraîcheur → on ne touche à rien."""
    lr = _lr(rocket_id=855, last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", None, "active",
        None, rockets, set(), summary,
    )
    assert lr.rocket_id == 855


async def test_ap_name_is_matched_despite_spaces_and_case(rockets):
    """Les noms UISP arrivent sales (` A2-HQ-SUD `) — un match strict perdrait le client."""
    lr = _lr(rocket_id=855, last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "  a2-dn1-sud1  ", None, "active",
        _now(), rockets, set(), summary,
    )
    assert lr.rocket_id == 1794


async def test_unknown_ap_leaves_the_row_alone(rockets):
    """AP absent de notre inventaire → aucun rattachement inventé."""
    lr = _lr(rocket_id=855, last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-AILLEURS-NORD", None, "active",
        _now(), rockets, set(), summary,
    )
    assert lr.rocket_id == 855
    assert summary["reparented"] == 0


async def test_ip_outside_the_management_plan_is_refused(rockets):
    """UISP remonte aussi des LAN de CPE — même garde-fou que la découverte.

    `_FakeSession.execute` lèverait si on tentait de libérer l'IP : la preuve
    que le filtre coupe AVANT toute écriture.
    """
    lr = _lr(rocket_id=1794, ip_address="10.135.4.13", last_discovered_at=None)
    summary = {"reparented": 0, "ip_updated": 0, "ip_conflict": 0}
    await _adopt_uisp_attribution(
        _FakeSession(), lr, "A2-DN1-SUD1", "192.168.10.1", "active",
        _now(), rockets, set(), summary,
    )
    assert lr.ip_address == "10.135.4.13"
    assert summary["ip_updated"] == 0


# ── Tests d'intégration (vraie fonction, vraie DB) ──────────────────────────
# Deux régressions vécues le 2026-07-22, toutes deux invisibles aux tests
# unitaires ci-dessus (qui fabriquent leur propre dict de résumé) :
#   1. les compteurs avaient été posés sur le résumé du sync INFRA → KeyError
#      au premier client rerattaché, tout le sync des stations tombait ;
#   2. l'IP était reprise même pour une station DÉCONNECTÉE, dont UISP n'a
#      qu'un dernier état connu — au 1er passage réel, `10.135.3.159` a été
#      attribuée à TROIS abonnés différents et `10.135.2.24` à deux, chacun
#      volant la ligne du précédent. Le vol d'IP, réintroduit par l'autre bout.


def _station(mac, name, ip, ap, status, seen_minutes_ago=60):
    return {
        "identification": {"id": f"sta-{mac}", "mac": mac, "name": name,
                           "modelName": "LiteBeam 5AC"},
        "overview": {
            "status": status,
            "lastSeen": (_now() - datetime.timedelta(minutes=seen_minutes_ago)).isoformat(),
            "wirelessMode": "sta-ptmp",
        },
        "mode": "router",
        "ipAddress": f"{ip}/16" if ip else None,
        "attributes": {"apDevice": {"name": ap}},
    }


def _fake_client(stations):
    class _C:
        def __init__(self, *a, **kw):
            pass

        async def fetch_devices(self, role=None):
            return stations

        async def fetch_data_links(self):
            return []
    return _C


async def _setup(db):
    old_ap = Rocket(name="ZZ-AT1-SUD1", location="ZZ AT1", radio_tech="airmax",
                    ip_address="10.99.200.1", status="up")
    new_ap = Rocket(name="A2-DN1-SUD1", location="A2 DN1", radio_tech="airmax",
                    ip_address="10.99.200.2", status="up")
    db.add_all([old_ap, new_ap])
    await db.flush()
    return old_ap, new_ap


async def _lr_row(db, mac, ip, rocket_id, days_since_radio=25):
    lr = Lr(name=f"client-{mac[-5:]}", model_variant="litebeam_5ac", status="down",
            ip_address=ip, mac_address=mac.lower(), rocket_id=rocket_id,
            location="ZZ AT1", auto_discovered=True,
            last_discovered_at=_now() - datetime.timedelta(days=days_since_radio))
    db.add(lr)
    await db.flush()
    return lr


async def test_down_but_recently_seen_client_gets_both_ap_and_ip(db, monkeypatch):
    """LE cas fondateur : « en outage depuis 1 h », et pourtant tout est bon.

    UISP est ici la source la PLUS RÉCENTE — c'est bien lui qui sait où est
    l'abonné et quelle adresse il porte. Une panne d'une heure ne périme pas un
    bail DHCP. Un critère binaire `uisp_status == "active"` aurait jeté une IP
    juste (vérifiée à la main sur l'interface airOS de l'équipement).
    """
    from app.services import uisp_sync_service

    old_ap, new_ap = await _setup(db)
    lr = await _lr_row(db, "1C:6A:1B:B8:79:B0", "10.135.5.152", old_ap.id)
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:B0", "Yakoub", "10.135.4.13", "A2-DN1-SUD1",
                 "disconnected", seen_minutes_ago=60),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["reparented"] == 1, "le résumé des STATIONS doit porter le compteur"
    assert summary["ip_updated"] == 1
    await db.flush()
    await db.refresh(lr)
    assert lr.rocket_id == new_ap.id
    assert lr.location == "A2 DN1"
    assert lr.ip_address == "10.135.4.13"


async def test_long_gone_client_is_reparented_but_keeps_its_ip(db, monkeypatch):
    """La limite : disparue depuis 3 semaines → l'AP se reprend, l'IP non.

    Un abonné ne change pas de site en étant éteint, donc le rattachement reste
    sûr. Son adresse, elle, a eu tout le temps d'être redonnée à quelqu'un
    d'autre par le DHCP — UISP n'en a plus qu'un souvenir.
    """
    from app.services import uisp_sync_service

    old_ap, new_ap = await _setup(db)
    lr = await _lr_row(db, "1C:6A:1B:B8:79:B0", "10.135.5.152", old_ap.id, days_since_radio=40)
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:B0", "Vieux", "10.135.4.13", "A2-DN1-SUD1",
                 "disconnected", seen_minutes_ago=60 * 24 * 21),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["reparented"] == 1
    assert summary["ip_updated"] == 0, "vue il y a 3 semaines : son IP est un souvenir"
    await db.flush()
    await db.refresh(lr)
    assert lr.rocket_id == new_ap.id
    assert lr.ip_address == "10.135.5.152"


async def test_active_client_gets_its_ip_back(db, monkeypatch):
    """UISP voit la station EN LIGNE → son IP est actuelle, on la reprend."""
    from app.services import uisp_sync_service

    old_ap, new_ap = await _setup(db)
    lr = await _lr_row(db, "1C:6A:1B:B8:79:B1", None, old_ap.id)
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:B1", "Actif", "10.135.4.14", "A2-DN1-SUD1", "active"),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["ip_updated"] == 1
    await db.flush()
    await db.refresh(lr)
    assert lr.ip_address == "10.135.4.14"


async def test_two_stations_claiming_the_same_ip_do_not_steal_it(db, monkeypatch):
    """LE bug de production : UISP a rendu la même IP pour plusieurs abonnés.

    La première la prend, la seconde est comptée en conflit et garde la sienne.
    Sans ce verrou, la seconde volait la ligne de la première, qui se
    retrouvait sans IP donc hors du sweep de ping — un client sain éteint par
    un autre.
    """
    from app.services import uisp_sync_service

    old_ap, _new_ap = await _setup(db)
    a = await _lr_row(db, "1C:6A:1B:B8:79:C1", None, old_ap.id)
    b = await _lr_row(db, "1C:6A:1B:B8:79:C2", None, old_ap.id)
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:C1", "A", "10.135.3.159", "A2-DN1-SUD1", "active"),
        _station("1C:6A:1B:B8:79:C2", "B", "10.135.3.159", "A2-DN1-SUD1", "active"),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["ip_updated"] == 1
    assert summary["ip_conflict"] == 1
    await db.flush()
    await db.refresh(a)
    await db.refresh(b)
    assert {a.ip_address, b.ip_address} == {"10.135.3.159", None}


async def test_ip_held_by_another_device_is_left_alone(db, monkeypatch):
    """IP déjà détenue en base → on s'abstient. Seul le radio voit le terrain.

    Ici on ne peut pas savoir laquelle des deux lignes est périmée ; voler,
    c'est laisser la victime sans IP.
    """
    from app.services import uisp_sync_service

    old_ap, _new_ap = await _setup(db)
    holder = await _lr_row(db, "1C:6A:1B:B8:79:D1", "10.135.7.77", old_ap.id)
    claimer = await _lr_row(db, "1C:6A:1B:B8:79:D2", None, old_ap.id)
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:D2", "Claimer", "10.135.7.77", "A2-DN1-SUD1", "active"),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["ip_updated"] == 0
    assert summary["ip_conflict"] == 1
    await db.flush()
    await db.refresh(holder)
    await db.refresh(claimer)
    assert holder.ip_address == "10.135.7.77"   # la victime garde son IP
    assert claimer.ip_address is None


# ── Suppression des stations déprovisionnées dans UISP (2026-07-23) ──────────
# UISP est la source de vérité du roster client : une station qu'il ne liste
# plus a été déprovisionnée délibérément → on supprime la ligne pour rester
# synchro. Garde-fous : seulement les LR issus de UISP (`uisp_synced_at` set),
# jamais un client radio-seul, et JAMAIS quand le roster revient vide.


async def _uisp_lr(db, mac, ip, rocket_id, *, blocked=False):
    """LR déjà connu de UISP (uisp_synced_at renseigné) — éligible à la purge."""
    lr = await _lr_row(db, mac, ip, rocket_id)
    lr.uisp_synced_at = _now() - datetime.timedelta(days=1)
    lr.uisp_ap_name = "ZZ-AT1-SUD1"
    lr.client_blocked = blocked
    await db.flush()
    return lr


async def test_uisp_sourced_station_gone_from_roster_is_deleted(db, monkeypatch):
    """LE cas demandé : `1c:6a:1b:b8:76:aa` retiré de UISP → supprimé chez nous."""
    from app.services import uisp_sync_service

    old_ap, _new_ap = await _setup(db)
    lr = await _uisp_lr(db, "1C:6A:1B:B8:76:AA", "10.135.6.10", old_ap.id)
    lr_id = lr.id
    # Roster NON vide (une autre station), mais sans notre MAC.
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:FF", "Autre", "10.135.6.11", "ZZ-AT1-SUD1", "active"),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["deleted"] == 1
    assert await db.get(Lr, lr_id) is None


async def test_radio_only_station_is_never_deleted(db, monkeypatch):
    """Client découvert par radio seul (uisp_synced_at NULL) → jamais purgé ici.

    C'est discovery_service qui le possède ; l'effacer déclencherait une
    recréation en boucle au prochain poll radio.
    """
    from app.services import uisp_sync_service

    old_ap, _new_ap = await _setup(db)
    lr = await _lr_row(db, "1C:6A:1B:B8:79:E1", "10.135.6.20", old_ap.id)  # pas de uisp_synced_at
    lr_id = lr.id
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:FF", "Autre", "10.135.6.11", "ZZ-AT1-SUD1", "active"),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["deleted"] == 0
    assert await db.get(Lr, lr_id) is not None


async def test_empty_roster_never_purges_the_client_base(db, monkeypatch):
    """Garde-fou anti-catastrophe : roster vide = échec de fetch, on ne supprime rien.

    Un payload vide ne doit JAMAIS être lu comme « tout le monde déprovisionné ».
    """
    from app.services import uisp_sync_service

    old_ap, _new_ap = await _setup(db)
    lr = await _uisp_lr(db, "1C:6A:1B:B8:76:AA", "10.135.6.10", old_ap.id)
    lr_id = lr.id
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["deleted"] == 0
    assert await db.get(Lr, lr_id) is not None


async def test_blocked_client_gone_from_roster_is_deleted_too(db, monkeypatch):
    """Déprovisionné = plus servi : on supprime même un client bloqué.

    Le journal FAI est un fichier par MAC → l'audit de la coupure survit.
    """
    from app.services import uisp_sync_service

    old_ap, _new_ap = await _setup(db)
    lr = await _uisp_lr(db, "1C:6A:1B:B8:76:AA", "10.135.6.10", old_ap.id, blocked=True)
    lr_id = lr.id
    monkeypatch.setattr(uisp_sync_service.uisp_service, "UISPClient", _fake_client([
        _station("1C:6A:1B:B8:79:FF", "Autre", "10.135.6.11", "ZZ-AT1-SUD1", "active"),
    ]))

    summary = await uisp_sync_service.sync_uisp_stations(db)

    assert summary["deleted"] == 1
    assert await db.get(Lr, lr_id) is None
