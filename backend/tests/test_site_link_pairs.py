"""Page Point-à-Point : appariement des deux bouts par le CÂBLAGE (MAC).

L'ancienne page recollait les bouts d'après le NOM et affichait « extrémité non
listée » pour cinq situations différentes. Ces tests verrouillent l'appariement
par ``site_links`` et la raison de l'état de chaque bout.
"""

from app.services.lr_health_service import build_site_link_pairs

AF_FLOOR = 1950.0
BH_FLOOR = 150.0

MAC_CT2 = "aa:aa:aa:aa:aa:01"
MAC_PK1 = "aa:aa:aa:aa:aa:02"


def _dev(id_, name, site, status="up", device_type="airfiber", distance=7400.0):
    return {"id": id_, "name": name, "ip": f"10.0.0.{id_}", "status": status,
            "site": site, "device_type": device_type, "distance_m": distance}


def _cable(mac_a=MAC_CT2, mac_b=MAC_PK1):
    return {"site_a": "A2 CT2", "site_b": "A2 PK1", "mac_a": mac_a.upper(),
            "mac_b": mac_b.upper(), "name_a": "F60 CT2-PK1", "name_b": "F60 PK1-CT2"}


def _run(cables, devices, metrics):
    return build_site_link_pairs(
        cables, devices, metrics, af60_floor=AF_FLOOR, airmax_floor=BH_FLOOR,
    )


def test_pairs_by_mac_and_takes_the_worst_measured_end():
    """Cas réel CT2↔PK1 : 1200,5 et 1275,5 → liaison dégradée à 1200,5."""
    devices = {MAC_CT2: _dev(1535, "F60 CT2-PK1", "A2 CT2"),
               MAC_PK1: _dev(1137, "F60 PK1-CT2", "A2 PK1")}
    metrics = {1535: {"total_capacity_mbps": 1200.5},
               1137: {"total_capacity_mbps": 1275.5}}
    degraded, unmeasured = _run([_cable()], devices, metrics)
    assert len(degraded) == 1 and not unmeasured
    link = degraded[0]
    assert link.capacity_mbps == 1200.5
    assert link.capacity_floor_mbps == AF_FLOOR
    assert {link.end_a.device_id, link.end_b.device_id} == {1535, 1137}
    assert link.end_a.state == link.end_b.state == "measured"


def test_down_end_is_named_and_its_stale_capacity_ignored():
    devices = {MAC_CT2: _dev(1, "F60 CT2-PK1", "A2 CT2", status="down"),
               MAC_PK1: _dev(2, "F60 PK1-CT2", "A2 PK1")}
    metrics = {1: {"total_capacity_mbps": 100.0},     # périmée : ne compte pas
               2: {"total_capacity_mbps": 1500.0}}
    degraded, _ = _run([_cable()], devices, metrics)
    assert degraded[0].capacity_mbps == 1500.0
    assert degraded[0].end_a.state == "down"


def test_other_end_states_are_distinguished():
    """Sans mesure / inconnu / non supervisé : trois raisons, trois états."""
    devices = {MAC_CT2: _dev(1, "F60 CT2-PK1", "A2 CT2")}
    metrics = {1: {"total_capacity_mbps": 1000.0}}
    degraded, _ = _run([_cable()], devices, metrics)
    assert degraded[0].end_b.state == "unsupervised"
    assert degraded[0].end_b.name == "F60 PK1-CT2"          # nom UISP

    devices[MAC_PK1] = _dev(2, "F60 PK1-CT2", "A2 PK1", status="unknown")
    assert _run([_cable()], devices, metrics)[0][0].end_b.state == "unknown"

    devices[MAC_PK1] = _dev(2, "F60 PK1-CT2", "A2 PK1")
    assert _run([_cable()], devices, metrics)[0][0].end_b.state == "no_data"


def test_healthy_link_is_not_listed():
    devices = {MAC_CT2: _dev(1, "a", "A2 CT2"), MAC_PK1: _dev(2, "b", "A2 PK1")}
    metrics = {1: {"total_capacity_mbps": 1951.0}, 2: {"total_capacity_mbps": 1960.0}}
    assert _run([_cable()], devices, metrics) == ([], [])


def test_link_without_any_measurement_is_unmeasured_never_healthy():
    devices = {MAC_CT2: _dev(1, "a", "A2 CT2", status="down"),
               MAC_PK1: _dev(2, "b", "A2 PK1")}
    degraded, unmeasured = _run([_cable()], devices, {})
    assert not degraded
    assert len(unmeasured) == 1
    assert unmeasured[0].degraded is False


def test_uncabled_radio_is_still_shown_alone():
    """Une radio que le câblage ignore ne doit pas disparaître de la page."""
    devices = {MAC_CT2: _dev(1, "F60 CT2-X", "A2 CT2")}
    degraded, _ = _run([], devices, {1: {"total_capacity_mbps": 800.0}})
    assert len(degraded) == 1
    assert degraded[0].end_b.state == "uncabled"


def test_ptp_litebeam_uses_its_own_floor():
    devices = {MAC_CT2: _dev(1, "PTP", "A2 CT2", device_type="ptp_litebeam"),
               MAC_PK1: _dev(2, "PTP", "A2 PK1", device_type="ptp_litebeam")}
    metrics = {1: {"total_capacity_mbps": 400.0}, 2: {"total_capacity_mbps": 133.0}}
    degraded, _ = _run([_cable()], devices, metrics)
    assert degraded[0].link_type == "airmax"
    assert degraded[0].capacity_floor_mbps == BH_FLOOR
    assert degraded[0].capacity_mbps == 133.0


def test_cable_without_any_p2p_radio_is_ignored():
    """Une fibre switch↔switch n'est pas un lien point-à-point radio."""
    devices = {MAC_CT2: _dev(1, "SW", "A2 CT2", device_type="uisp_switch"),
               MAC_PK1: _dev(2, "SW", "A2 PK1", device_type="uisp_switch")}
    assert _run([_cable()], devices, {}) == ([], [])
