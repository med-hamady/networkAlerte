"""
Unit tests for ssh_service pure helpers — no network, no paramiko transport.

Covers _parse_board_model, which extracts the hardware model from an airOS
/etc/board.info dump so an airMAX LR mis-inferred as the wrong variant (M5 vs
5AC) self-heals from the device itself. Board.info is the only model source for
airOS-M LRs (M5), which do not answer the HTTP status.cgi the AC firmware does.
"""

import json

from app.services.ssh_service import _parse_board_model, _parse_wstalist_metrics


def test_parse_board_model_m5():
    board_info = (
        "board.sysid=0xe835\n"
        "board.name=LiteBeam M5\n"
        "board.shortname=LBE-M5\n"
        "board.hwaddr=DC:9F:DB:00:00:00\n"
    )
    assert _parse_board_model(board_info) == "LiteBeam M5"


def test_parse_board_model_5ac():
    board_info = (
        "board.sysid=0xe7b5\n"
        "board.name=LiteBeam 5AC Gen2\n"
        "board.shortname=LBE-5AC-Gen2\n"
    )
    assert _parse_board_model(board_info) == "LiteBeam 5AC Gen2"


def test_parse_board_model_shortname_fallback():
    # No board.name → falls back to board.shortname.
    assert _parse_board_model("board.shortname=LBE-M5\n") == "LBE-M5"


def test_parse_board_model_empty_or_garbage():
    assert _parse_board_model("") is None
    assert _parse_board_model("no equals signs here") is None
    assert _parse_board_model("board.other=x\n") is None


# Real wstalist entry captured on the LiteBeam M5 (10.135.6.37, XW.v6.3.24)
# on 2026-07-15 — the AP it is linked to, trimmed to the fields the parser reads.
_REAL_WSTALIST = json.dumps([
    {
        "mac": "70:A7:41:4C:D5:29",
        "signal": -55,
        "rssi": 41,
        "ccq": 99,
        "tx": 19.5,
        "rx": 58.5,
        "noisefloor": -103,
        "uptime": 40170,
        "airmax": {"quality": 0, "capacity": 0},
        "stats": {"rx_bytes": 366908719, "tx_bytes": 12437780},
        "remote": {"signal": -60, "hostname": "A2-PK1-NORD1"},
    }
])


def test_parse_wstalist_maps_radio_metrics():
    m = _parse_wstalist_metrics(_REAL_WSTALIST)
    assert m["signal_dbm"] == -55
    assert m["noise_dbm"] == -103
    assert m["ccq_pct"] == 99
    assert m["uptime_seconds"] == 40170
    assert m["remote_signal_dbm"] == -60
    # CINR ≈ SNR = signal − noise floor = -55 − (-103) = 48
    assert m["cinr_db"] == 48.0


def test_parse_wstalist_no_station_or_bad_json():
    empty = _parse_wstalist_metrics("[]")
    assert all(v is None for v in empty.values())
    assert all(v is None for v in _parse_wstalist_metrics("not json").values())
    assert all(v is None for v in _parse_wstalist_metrics("{}").values())


def test_parse_wstalist_missing_fields_stay_none():
    # A station entry with only a signal — everything else stays None, and CINR
    # is not derived without a noise floor.
    m = _parse_wstalist_metrics(json.dumps([{"signal": -50}]))
    assert m["signal_dbm"] == -50
    assert m["cinr_db"] is None
    assert m["ccq_pct"] is None


def test_wstalist_owns_quality_and_the_consumption_counters():
    """`wstalist` fournit la QUALITÉ et les COMPTEURS — pas la capacité.

    Répartition des sources après la bascule vers le poll par l'AP :
      - capacité / débit / potentiel  → l'AP ;
      - compteurs d'octets            → ICI, et nulle part ailleurs. La conso
        est facturée : elle garde le compteur du CPE, sa source depuis toujours.
        Le compteur de l'AP pour la même station est un cumul d'une AUTRE
        origine (55 Gio contre 2 Gio sur un même client au même instant) :
        changer de source ferait facturer l'écart au client.
      - qualité (signal/bruit/CINR/CCQ) → ICI pour les M5 seulement, car l'AP
        n'expose aucun CCQ par station et annonce un CINR de 3 dB là où le SNR
        réel est de 25 dB. Sur un 5AC, l'appelant ne retient que les compteurs.

    La capacité ne doit JAMAIS revenir ici : deux sources sur la même clé
    collapse feraient osciller la valeur d'un cycle à l'autre.
    """
    m = _parse_wstalist_metrics(_REAL_WSTALIST)
    for absent in ("dl_capacity_mbps", "ul_capacity_mbps",
                   "dl_throughput_mbps", "ul_throughput_mbps",
                   "dl_phy_rate_mbps", "ul_phy_rate_mbps"):
        assert absent not in m, f"{absent} ne doit pas venir du SSH"
    # Compteurs : source de la consommation.
    assert m["radio_rx_bytes"] == 366908719
    assert m["radio_tx_bytes"] == 12437780
    # Qualité : ce que l'AP ne sait pas donner d'un M5.
    assert m["ccq_pct"] == 99
    assert m["cinr_db"] == 48.0
