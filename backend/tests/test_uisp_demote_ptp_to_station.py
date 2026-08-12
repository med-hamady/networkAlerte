"""
Rétrogradation d'un `ptp_litebeam` redevenu abonné — et ce qu'elle NE doit pas toucher.

Une LiteBeam déposée d'un mât P2P et réinstallée chez un client change de nature
sans changer d'identité (même MAC, même ligne `devices`). Aucun des trois chemins
du projet ne reprenait ce changement, chacun refusant pour une raison valable :
`classify_device` ne classe plus un `sta-ptmp` (donc le sync infra ne la regarde
plus, et la promotion est à sens unique), `sync_uisp_stations` la rejette en
`type_conflict`, et `discovery_service._mac_held_by_non_lr` refuse de créer un LR
sur une MAC d'infra. Personne ne possédait la rétrogradation.

Constaté le 2026-08-12 sur `1C:6A:1B:B6:36:F8` : figée 7 jours sur « A2 TJN1 » avec
une IP morte, pendant que UISP la donnait active chez un client sous A2-TS1-OMNI.
La ligne fantôme était comptée dans la capacité infra du site et pingée comme de
l'infra (`device_unreachable` notifié sur WhatsApp pour un équipement sain), tandis
que l'abonné n'existait nulle part — donc incoupable par le système de paiement.

⚠️ LE TEST QUI COMPTE LE PLUS EST `test_af60_station_end_is_never_demoted` : un AF60
annonce `role=station` à un bout de CHAQUE lien P2P, c'est son état normal. Le
rétrograder arracherait un backhaul de l'infra à chaque sync.
"""

import pytest

from app.models.device import AirFiber, Lr, PtpLiteBeam, Rocket, UispSwitch
from app.services import discovery_service, uisp_sync_service
from app.services.uisp_sync_service import _demote_reclassified_stations

PTP_MAC = "1C:6A:1B:B6:36:F8"


def _uisp_device(mac: str, *, role: str, mode: str | None, model: str = "LBE-5AC-Gen2") -> dict:
    return {
        "identification": {"mac": mac, "role": role, "model": model, "name": "CPE"},
        "overview": {"wirelessMode": mode} if mode is not None else {},
    }


def _ptp(mac: str = PTP_MAC) -> PtpLiteBeam:
    dev = PtpLiteBeam(
        name="LiteBeam TJN1-DN1", ip_address="10.135.170.1",
        mac_address=mac, location="A2 TJN1",
    )
    dev.id = 4242
    return dev


class _FakeSession:
    """Aucune écriture n'est attendue : la conversion est monkeypatchée."""

    async def execute(self, *a, **k):
        raise AssertionError("aucune requête ne doit partir dans ces cas")

    async def flush(self):
        return None


@pytest.fixture
def converted(monkeypatch) -> list[tuple[int, str]]:
    """Enregistre les (device_id, model_variant) réellement convertis."""
    calls: list[tuple[int, str]] = []

    async def _fake(session, dev, variant):
        calls.append((dev.id, variant))

    monkeypatch.setattr(uisp_sync_service, "_convert_ptp_litebeam_to_lr", _fake)
    return calls


async def test_ptp_litebeam_reported_as_subscriber_is_demoted(converted):
    """Le cas fondateur : UISP le donne `role=station` + `sta-ptmp`."""
    dev = _ptp()
    payload = [_uisp_device(PTP_MAC, role="station", mode="sta-ptmp")]

    demoted = await _demote_reclassified_stations(
        _FakeSession(), payload, [dev], dry_run=False,
    )

    assert [d["mac"] for d in demoted] == [PTP_MAC]
    assert converted == [(4242, "litebeam_5ac")]


async def test_af60_station_end_is_never_demoted(converted):
    """⚠️ Un AF60 est `role=station` à un bout de chaque lien P2P — état NORMAL.

    Le rétrograder sortirait un backhaul de l'infra à chaque sync. La garantie est
    STRUCTURELLE (seul `device_type == "ptp_litebeam"` est candidat), pas un filtre
    sur le payload : même si UISP annonçait l'AF60 en `sta-ptmp`, rien ne bouge.
    """
    af60 = AirFiber(name="F60 CT1-NR1", mac_address="AA:BB:CC:00:00:01")
    af60.id = 77
    payload = [
        _uisp_device("AA:BB:CC:00:00:01", role="station", mode="sta-ptmp", model="AF60-LR"),
    ]

    demoted = await _demote_reclassified_stations(
        _FakeSession(), payload, [af60], dry_run=False,
    )

    assert demoted == []
    assert converted == []


async def test_rockets_and_switches_are_never_demoted(converted):
    """Rien ne transforme un AP ou un switch en CPE — même annoncés `station`."""
    rocket = Rocket(name="A2-TS1-OMNI", mac_address="AA:BB:CC:00:00:02", radio_tech="airmax")
    switch = UispSwitch(name="TS1-UISP-S", mac_address="AA:BB:CC:00:00:03")
    rocket.id, switch.id = 88, 99
    payload = [
        _uisp_device("AA:BB:CC:00:00:02", role="station", mode="sta-ptmp"),
        _uisp_device("AA:BB:CC:00:00:03", role="station", mode="sta-ptmp"),
    ]

    demoted = await _demote_reclassified_stations(
        _FakeSession(), payload, [rocket, switch], dry_run=False,
    )

    assert demoted == []
    assert converted == []


async def test_absence_from_the_payload_demotes_nothing(converted):
    """JAMAIS sur une absence — seule une affirmation positive rétrograde.

    Même règle que l'attribution des ports de switch : une MAC qui a disparu ne
    dit pas ce qu'est devenu l'équipement, elle ne dit rien du tout.
    """
    dev = _ptp()
    demoted = await _demote_reclassified_stations(
        _FakeSession(), [], [dev], dry_run=False,
    )
    assert demoted == []
    assert converted == []


@pytest.mark.parametrize("mode", [None, "", "ap-ptp", "sta-ptp"])
async def test_station_role_without_ptmp_mode_demotes_nothing(converted, mode):
    """`role=station` seul ne suffit pas : le mode radio doit l'affirmer.

    Un `sta-ptp` EST l'extrémité d'un lien point-à-point — c'est exactement ce
    qu'un `ptp_litebeam` doit rester. Un mode absent n'affirme rien.
    """
    dev = _ptp()
    payload = [_uisp_device(PTP_MAC, role="station", mode=mode)]

    demoted = await _demote_reclassified_stations(
        _FakeSession(), payload, [dev], dry_run=False,
    )

    assert demoted == []
    assert converted == []


async def test_ap_role_demotes_nothing(converted):
    """Une LiteBeam PTP « Main » garde `role=ap` — elle n'est pas un abonné."""
    dev = _ptp()
    payload = [_uisp_device(PTP_MAC, role="ap", mode="ap-ptp")]

    demoted = await _demote_reclassified_stations(
        _FakeSession(), payload, [dev], dry_run=False,
    )

    assert demoted == []
    assert converted == []


async def test_dry_run_reports_without_writing(converted):
    """La prévisualisation nomme la ligne concernée et n'écrit rien."""
    dev = _ptp()
    payload = [_uisp_device(PTP_MAC, role="station", mode="sta-ptmp")]

    demoted = await _demote_reclassified_stations(
        _FakeSession(), payload, [dev], dry_run=True,
    )

    assert len(demoted) == 1
    assert demoted[0]["site"] == "A2 TJN1"       # l'ancien site, pour le rapport
    assert demoted[0]["variant"] == "litebeam_5ac"
    assert converted == []                        # aucune conversion réelle


async def test_m5_keeps_its_own_variant(converted):
    """Le variant suit le modèle annoncé — un M5 ne doit pas devenir un 5AC."""
    dev = _ptp()
    payload = [_uisp_device(PTP_MAC, role="station", mode="sta-ptmp", model="LBE-M5-23")]

    await _demote_reclassified_stations(_FakeSession(), payload, [dev], dry_run=False)

    assert converted == [(4242, "litebeam_m5")]


async def test_conversion_really_moves_the_row_to_lrs(db):
    """Le SQL brut de la conversion, contre une VRAIE base.

    Une sous-classe se déplace ici à la main (INSERT ... SELECT + DELETE + UPDATE
    du discriminant), donc rien ne rattrape une colonne NOT NULL oubliée : c'est
    la base qui tranche, pas le typage Python. On vérifie aussi que l'identité
    (`devices.id`, MAC) est PRÉSERVÉE — c'est tout l'intérêt de convertir plutôt
    que de supprimer/recréer : les métriques, l'historique et le journal FAI de
    l'abonné restent accrochés à la même ligne.
    """
    from sqlalchemy import text

    dev = PtpLiteBeam(
        name="LiteBeam TJN1-DN1", ip_address="10.99.201.7", status="down",
        mac_address="1c:6a:1b:00:99:01", location="ZZ TJN1",
        ssh_username="ubnt", ssh_password="secret", ssh_port=443, distance_m=1200.0,
    )
    db.add(dev)
    await db.flush()
    did = dev.id

    await uisp_sync_service._convert_ptp_litebeam_to_lr(db, dev, "litebeam_5ac")
    await db.flush()

    row = (await db.execute(text(
        "SELECT d.device_type, d.mac_address, d.auto_discovered, l.model_variant, "
        "l.ssh_username, l.ssh_password, l.ssh_port, l.distance_m, l.lan_interface, "
        "l.rocket_id, l.client_blocked, l.block_mode, l.topology_mode "
        "FROM devices d JOIN lrs l ON l.id = d.id WHERE d.id = :id"
    ), {"id": did})).one()

    assert row.device_type == "lr"
    assert row.mac_address == "1c:6a:1b:00:99:01"   # identité préservée
    assert row.auto_discovered is True
    assert row.model_variant == "litebeam_5ac"
    assert (row.ssh_username, row.ssh_password, row.ssh_port) == ("ubnt", "secret", 443)
    assert row.distance_m == 1200.0
    assert row.lan_interface == "eth0"              # airMAX (cf. default_lan_interface)
    assert row.rocket_id is None                    # posé par sync_uisp_stations, après
    assert (row.client_blocked, row.block_mode, row.topology_mode) == (
        False, "full", "unknown",
    )
    # L'ancienne sous-table ne doit plus rien porter, sinon l'équipement resterait
    # visible comme infra par toute requête qui joint `ptp_litebeams`.
    left = (await db.execute(
        text("SELECT count(*) FROM ptp_litebeams WHERE id = :id"), {"id": did},
    )).scalar_one()
    assert left == 0


async def test_after_demotion_the_ap_poll_adopts_the_row(db):
    """La jonction : une fois converti, le poll de l'AP reprend la ligne.

    L'AP rapporte cette station à CHAQUE cycle (60 s) et `reconcile_peers` est
    appelé pour toutes ses stations — c'est justement là que ça bloquait :
    `_mac_held_by_non_lr` refusait d'agir sur une MAC d'infra, à raison. La
    conversion ne fait que débloquer ce chemin, elle ne le remplace pas.

    C'est important que ce soit le RADIO qui finisse le travail et pas seulement
    le sync quotidien : lui seul peut libérer une IP tenue par une ligne périmée
    (`_release_ip_if_held`), là où le sync UISP s'abstient toujours. Et l'IP
    corrigée fait repartir le ping (qui ne filtre pas sur le statut), donc le
    poll direct, donc les métriques et la consommation de l'abonné.
    """
    ap = Rocket(
        name="ZZ-TS1-OMNI", ip_address="10.99.202.1", status="up",
        location="ZZ TS1", radio_tech="airmax",
    )
    dev = PtpLiteBeam(
        name="LiteBeam TJN1-DN1", ip_address="10.135.170.9", status="down",
        mac_address="1c:6a:1b:00:99:02", location="ZZ TJN1",
    )
    db.add_all([ap, dev])
    await db.flush()
    did = dev.id

    await uisp_sync_service._convert_ptp_litebeam_to_lr(db, dev, "litebeam_5ac")
    await db.flush()

    # Ce que l'AP annonce au cycle suivant (cf. le fan-out de airos_api_poll_job).
    await discovery_service.reconcile_peers(db, ap, [{
        "mac": "1C:6A:1B:00:99:02",          # l'AP l'annonce en majuscules
        "mgmt_ip": "10.135.1.121",
        "hostname": "38302954-El ghalya henoune Bouthiere",
        "model": "LiteBeam 5AC",
        "firmware": None,
    }])
    await db.flush()

    lr = await db.get(Lr, did)
    assert lr is not None                    # même ligne, pas une recréation
    assert lr.rocket_id == ap.id             # rattaché à son AP réel
    assert lr.location == "ZZ TS1"           # le site suit l'AP
    assert lr.ip_address == "10.135.1.121"   # l'IP morte est remplacée
    assert lr.last_discovered_at is not None


async def test_missing_model_falls_back_to_airmax_not_ltu(converted):
    """Sans chaîne de modèle, le repli doit rester airMAX.

    `_infer_model_variant` retombe sur `ltu_lr` quand le parent n'est pas un
    Rocket airMAX — or un `ptp_litebeam` est un airMAX par construction
    (`classify_device` ne le produit que pour `uisp_type == "airMax"`).
    """
    dev = _ptp()
    payload = [_uisp_device(PTP_MAC, role="station", mode="sta-ptmp", model="")]

    await _demote_reclassified_stations(_FakeSession(), payload, [dev], dry_run=False)

    assert converted == [(4242, "litebeam_5ac")]
