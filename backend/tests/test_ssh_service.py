"""
Unit tests for ssh_service pure helpers — no network, no paramiko transport.

Covers _parse_board_model, which extracts the hardware model from an airOS
/etc/board.info dump so an airMAX LR mis-inferred as the wrong variant (M5 vs
5AC) self-heals from the device itself. Board.info is the only model source for
airOS-M LRs (M5), which do not answer the HTTP status.cgi the AC firmware does.
"""

import json

import paramiko
import pytest

from app.services import ssh_service
from app.services.ssh_service import (
    _FingerprintMismatchError,
    _host_key_rotation_confirmed,
    _open_transport,
    _parse_board_model,
    _parse_wstalist_metrics,
)


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


# ── Auto-guérison de la clé d'hôte (LR re-flashé) ───────────────────────────
#
# Un LR ré-initialisé régénère sa clé SSH mais garde sa MAC (gravée). La
# nouvelle clé n'est ré-épinglée que si la MAC attendue est présente sur
# l'équipement — preuve que c'est le même matériel, pas un MITM ni une IP DHCP
# réattribuée. Invérifiable ⇒ on refuse (asymétrie voulue avec identity_refusal).

_EXPECTED_MAC = "d0:21:f9:f6:06:99"


class _FakeTransport:
    def __init__(self, sock, auth_ok=True):
        self._auth_ok = auth_ok
        self.closed = False

    def start_client(self, timeout=None):
        pass

    def get_remote_server_key(self):
        return object()

    def auth_password(self, username, password, fallback=False):
        if not self._auth_ok:
            raise paramiko.AuthenticationException("bad password")

    def close(self):
        self.closed = True


@pytest.fixture
def _patch_transport(monkeypatch):
    """Patche socket + paramiko.Transport + _fingerprint pour tester la logique
    de _open_transport sans réseau. Retourne un setter (observed_fp, device_macs)."""
    state = {"observed": "FP_NEW", "macs": set(), "last": None}

    def _fake_conn(addr, timeout=None):
        return object()

    def _fake_transport(sock):
        t = _FakeTransport(sock)
        state["last"] = t
        return t

    monkeypatch.setattr(ssh_service.socket, "create_connection", _fake_conn)
    monkeypatch.setattr(ssh_service.paramiko, "Transport", _fake_transport)
    monkeypatch.setattr(ssh_service, "_fingerprint", lambda key: state["observed"])
    monkeypatch.setattr(ssh_service, "_device_macs", lambda t, timeout=10: state["macs"])
    return state


def test_matching_fingerprint_returns_transport(_patch_transport):
    _patch_transport["observed"] = "FP_OK"
    transport, fp, pw = _open_transport(
        "10.0.0.1", 22, "ubnt", "pw", expected_fingerprint="FP_OK",
    )
    assert fp == "FP_OK"
    assert pw == "pw"


def test_first_seen_key_is_pinned_tofu(_patch_transport):
    # expected_fingerprint None → TOFU: accept whatever the host presents.
    transport, fp, pw = _open_transport(
        "10.0.0.1", 22, "ubnt", "pw", expected_fingerprint=None,
    )
    assert fp == "FP_NEW"


def test_rotated_key_self_heals_when_mac_confirms(_patch_transport):
    # Key changed (FP_NEW ≠ FP_OLD) but the expected MAC is on the device → the
    # re-flashed LR is the same box → accept the new key, do not raise.
    _patch_transport["observed"] = "FP_NEW"
    _patch_transport["macs"] = {_EXPECTED_MAC, "aa:bb:cc:dd:ee:ff"}
    transport, fp, pw = _open_transport(
        "10.0.0.1", 22, "ubnt", "pw",
        expected_fingerprint="FP_OLD", expected_mac=_EXPECTED_MAC,
    )
    assert fp == "FP_NEW"
    assert transport is _patch_transport["last"]
    assert transport.closed is False


def test_rotated_key_refused_when_mac_absent(_patch_transport):
    # Key changed and the device's MACs do NOT include the expected one → a
    # different device on a reassigned IP (or a MITM) → keep refusing.
    _patch_transport["observed"] = "FP_NEW"
    _patch_transport["macs"] = {"aa:bb:cc:dd:ee:ff"}
    with pytest.raises(_FingerprintMismatchError):
        _open_transport(
            "10.0.0.1", 22, "ubnt", "pw",
            expected_fingerprint="FP_OLD", expected_mac=_EXPECTED_MAC,
        )
    assert _patch_transport["last"].closed is True


def test_rotated_key_refused_when_no_expected_mac(_patch_transport):
    # No MAC to verify against → cannot confirm identity → strict old behaviour.
    _patch_transport["observed"] = "FP_NEW"
    with pytest.raises(_FingerprintMismatchError):
        _open_transport(
            "10.0.0.1", 22, "ubnt", "pw", expected_fingerprint="FP_OLD",
        )


def test_rotated_key_refused_when_macs_unreadable(_patch_transport):
    # Firmware exposes no MAC (empty set) → unverifiable → refuse (we only trust
    # a rotated key on POSITIVE proof, the inverse of the action guard).
    _patch_transport["observed"] = "FP_NEW"
    _patch_transport["macs"] = set()
    with pytest.raises(_FingerprintMismatchError):
        _open_transport(
            "10.0.0.1", 22, "ubnt", "pw",
            expected_fingerprint="FP_OLD", expected_mac=_EXPECTED_MAC,
        )


def test_host_key_rotation_confirmed_helper(monkeypatch):
    monkeypatch.setattr(
        ssh_service, "_device_macs", lambda t, timeout=10: {_EXPECTED_MAC}
    )
    assert _host_key_rotation_confirmed(object(), _EXPECTED_MAC) is True
    assert _host_key_rotation_confirmed(object(), "00:00:00:00:00:00") is False
    assert _host_key_rotation_confirmed(object(), None) is False
    monkeypatch.setattr(ssh_service, "_device_macs", lambda t, timeout=10: set())
    assert _host_key_rotation_confirmed(object(), _EXPECTED_MAC) is False


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
