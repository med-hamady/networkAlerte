"""
Unit tests for airos_api_service.parse_airos_link_metrics — pure Python, no DB.

The fixture is a copy of a real status.cgi response captured on a LiteBeam 5AC
(fw v8.7.22).

⚠ It used to be trimmed to "only the fields the parser reads", which made it
actively misleading: `wireless.throughput` was absent from the fixture purely
because the parser ignored it, and that absence was later read as proof the
firmware did not expose throughput at all. It does. Keep whole blocks here even
when unused, so the fixture stays evidence of what the device sends rather than
a mirror of what the code already knows.
"""

from app.services.airos_api_service import (
    _extract_hostname,
    _extract_model,
    _extract_netrole,
    airmax_variant_from_model,
    parse_airos_link_metrics,
)


def _real_status() -> dict:
    return {
        "host": {"hostname": "44910449- Habib Khoumeini", "uptime": 9455, "netrole": "router"},
        "wireless": {
            # Débit RÉEL du lien (kbps), hors du bloc `sta`. Sur le CPE, rx =
            # descendant client, tx = montant. Trois ordres de grandeur sous la
            # capacité ci-dessous : c'est le point du test.
            "throughput": {"tx": 115, "rx": 186},
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
    # CAPACITÉ réelle DL/UL — ce que le lien pourrait écouler
    assert m["dl_capacity_mbps"] == 145.08
    assert m["ul_capacity_mbps"] == 146.64
    # DÉBIT réel — le trafic écoulé, sans commune mesure avec la capacité.
    # C'est la régression que ce test verrouille : tant que le débit était lu
    # dans `capacity.*`, la fiche affichait 145 Mbps de « débit » pour 186 kbps
    # de trafic réel.
    assert m["dl_throughput_mbps"] == 0.186
    assert m["ul_throughput_mbps"] == 0.115
    assert m["dl_throughput_mbps"] < m["dl_capacity_mbps"] / 100
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


def test_extract_model():
    assert _extract_model({"host": {"devmodel": "LBE-M5-23"}}) == "LBE-M5-23"
    assert _extract_model({"host": {"model": "LiteBeam 5AC Gen2"}}) == "LiteBeam 5AC Gen2"
    # devmodel wins over model when both present
    assert _extract_model({"host": {"devmodel": "LBE-M5-23", "model": "x"}}) == "LBE-M5-23"
    assert _extract_model({"host": {}}) is None
    assert _extract_model({}) is None


def test_airmax_variant_from_model():
    assert airmax_variant_from_model("LBE-M5-23") == "litebeam_m5"
    assert airmax_variant_from_model("LiteBeam M5") == "litebeam_m5"
    assert airmax_variant_from_model("LBE-5AC-Gen2") == "litebeam_5ac"
    assert airmax_variant_from_model("LiteBeam 5AC Gen2") == "litebeam_5ac"
    # ambiguous / absent → None (leave the current variant untouched)
    assert airmax_variant_from_model("") is None
    assert airmax_variant_from_model(None) is None
    assert airmax_variant_from_model("PowerBeam") is None


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
