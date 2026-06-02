"""
Unit tests for airos_api_service.parse_airos_link_metrics — pure Python, no DB.

The fixture is a trimmed copy of a real status.cgi response captured on a
LiteBeam 5AC (fw v8.7.22) on 2026-06-02, keeping only the fields the parser
reads.
"""

from app.services.airos_api_service import (
    _extract_hostname,
    _extract_netrole,
    parse_airos_link_metrics,
)


def _real_status() -> dict:
    return {
        "host": {"hostname": "44910449- Habib Khoumeini", "uptime": 9455, "netrole": "router"},
        "wireless": {
            "polling": {"cb_capacity": 145860},
            "sta": [
                {
                    "signal": -44,
                    "distance": 600,
                    "rx_idx": 8,
                    "tx_idx": 8,
                    "dl_capacity_expect": 156000,
                    "ul_capacity_expect": 156000,
                    "dl_linkscore": 93,
                    "ul_linkscore": 94,
                    "dl_avg_linkscore": 94,
                    "ul_avg_linkscore": 90,
                    "stats": {"rx_bytes": 261795616, "tx_bytes": 17800863},
                    "airmax": {
                        "cb_capacity": 145860,
                        "dl_capacity": 145080,
                        "ul_capacity": 146640,
                        "rx": {"cinr": 31},
                        "tx": {"cinr": 29},
                    },
                    "remote": {"signal": -49},
                }
            ],
        },
    }


def test_parse_real_status_maps_dashboard_values():
    m = parse_airos_link_metrics(_real_status())

    # Link Potential = mean(dl_linkscore, ul_linkscore)
    assert m["link_potential_pct"] == 93.5
    # Total Capacity = cb_capacity / 1000
    assert m["total_capacity_mbps"] == 145.86
    # Actual DL/UL capacity
    assert m["tx_rate_mbps"] == 145.08
    assert m["rx_rate_mbps"] == 146.64
    # Ideal (expected) capacity — enables capacity_low rules on airMAX
    assert m["tx_ideal_mbps"] == 156.0
    assert m["rx_ideal_mbps"] == 156.0
    # Rate index "Nx"
    assert m["local_rx_rate_idx"] == 8
    assert m["remote_rx_rate_idx"] == 8
    # Signal / CINR
    assert m["signal_dbm"] == -44
    assert m["cinr_db"] == 31
    assert m["ul_cinr_db"] == 29
    assert m["remote_signal_dbm"] == -49
    # Misc
    assert m["distance_m"] == 600
    assert m["radio_rx_bytes"] == 261795616
    assert m["radio_tx_bytes"] == 17800863
    assert m["uptime_seconds"] == 9455


def test_extract_hostname():
    assert _extract_hostname(_real_status()) == "44910449- Habib Khoumeini"


def test_extract_netrole():
    # Real capture is a router.
    assert _extract_netrole(_real_status()) == "router"
    assert _extract_netrole({"host": {"netrole": "Bridge"}}) == "bridge"  # normalized
    # Unknown / missing / unexpected → None (never erases a known state).
    assert _extract_netrole({"host": {"netrole": "ap"}}) is None
    assert _extract_netrole({"host": {}}) is None
    assert _extract_netrole({}) is None


def test_link_potential_falls_back_to_avg_linkscore():
    raw = _real_status()
    sta = raw["wireless"]["sta"][0]
    del sta["dl_linkscore"]
    del sta["ul_linkscore"]
    m = parse_airos_link_metrics(raw)
    # mean(dl_avg=94, ul_avg=90)
    assert m["link_potential_pct"] == 92.0


def test_total_capacity_fallback_to_polling():
    raw = _real_status()
    del raw["wireless"]["sta"][0]["airmax"]["cb_capacity"]
    m = parse_airos_link_metrics(raw)
    assert m["total_capacity_mbps"] == 145.86  # from wireless.polling.cb_capacity


def test_no_station_returns_all_none_metrics():
    raw = {"host": {"hostname": "x", "uptime": 10}, "wireless": {"sta": []}}
    m = parse_airos_link_metrics(raw)
    assert m["uptime_seconds"] == 10  # host-level still parsed
    assert m["signal_dbm"] is None
    assert m["link_potential_pct"] is None
    assert m["total_capacity_mbps"] is None
