"""Unit tests for ltu_api_service peer parsing — pure Python, no DB."""

from app.services.ltu_api_service import (
    _extract_peer_net_mode,
    parse_all_peers_info,
)


def _peer(net_mode="router", mac="D0:21:F9:F6:0B:91"):
    return {
        "common": {
            "mgmtIp": "10.135.8.159",
            "hostname": "LR-test",
            "identification": {"model": "LTU-LR", "firmwareVersion": "v2.4", "mac": mac},
        },
        "remote": [{"netMode": net_mode, "linkQuality": {}}],
    }


def test_extract_net_mode_router_and_bridge():
    assert _extract_peer_net_mode(_peer("router")) == "router"
    assert _extract_peer_net_mode(_peer("Bridge")) == "bridge"  # normalized


def test_extract_net_mode_unknown_or_missing_is_none():
    assert _extract_peer_net_mode(_peer("ap")) is None
    assert _extract_peer_net_mode({"remote": []}) is None
    assert _extract_peer_net_mode({}) is None


def test_parse_all_peers_info_includes_net_mode():
    raw = {"wireless": {"peers": [_peer("bridge"), _peer("router", "AA:BB:CC:DD:EE:FF")]}}
    peers = parse_all_peers_info(raw)
    assert len(peers) == 2
    assert peers[0]["net_mode"] == "bridge"
    assert peers[1]["net_mode"] == "router"
    # MAC normalized to lowercase colon form (used as the fan-out key).
    assert peers[0]["mac"] == "d0:21:f9:f6:0b:91"
