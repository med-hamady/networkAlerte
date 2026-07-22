"""
Lecture des clients d'un AP airMAX — `airos_api_service.parse_airos_ap_stations`.

Un seul appel à un Rocket rend TOUS ses abonnés, au lieu d'un appel par
LiteBeam. Ce qui rend la bascule sûre, c'est que les deux parsers (côté AP et
côté CPE) produisent les mêmes clés avec le même sens.

La fixture `fixtures/airos_ap_status.json` est une capture RÉELLE d'un Rocket
ap-ptmp (fw v8.7.22, 10.135.144.2, 2026-07-21), réduite à 2 de ses 14 clients
— un LiteBeam 5AC (airOS 8) et un LiteBeam M5 (airOS 6) — mais dont chaque
entrée `sta[]` est conservée ENTIÈRE. On ne coupe que le nombre de stations,
jamais les champs : une fixture doit rester la preuve de ce que l'équipement
envoie, pas le miroir de ce que le parser sait lire.

Le point dur couvert ici est le sens des étiquettes, vérifié sur l'équipement
en interrogeant un AP et l'un de ses CPE coup sur coup (3 mesures) :
  - `dl_*` / `ul_*` sont ABSOLUS   → identiques des deux côtés
  - `rx` / `tx`     sont RELATIFS  → doivent être croisés côté AP
"""

import json
from pathlib import Path

from app.services.airos_api_service import parse_airos_ap_stations

_FIXTURE = Path(__file__).parent / "fixtures" / "airos_ap_status.json"


def _ap_status() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _by_platform(needle: str) -> dict:
    raw = _ap_status()
    for sta, (_mac, metrics, _meta) in zip(raw["wireless"]["sta"],
                                           parse_airos_ap_stations(raw), strict=True):
        if needle in str((sta.get("remote") or {}).get("platform")):
            return metrics
    raise AssertionError(f"aucune station {needle} dans la fixture")


def test_returns_one_entry_per_station_keyed_by_mac():
    stations = parse_airos_ap_stations(_ap_status())
    assert len(stations) == 2
    for mac, _m, _meta in stations:
        # identité = MAC normalisée, comme le fan-out LTU et discovery_service
        assert mac and mac == mac.lower() and ":" in mac


def test_capacity_labels_are_absolute():
    """`dl_capacity`/`ul_capacity` se lisent tels quels, sans croisement.

    Vérifié terrain : l'AP et le CPE renvoient des valeurs identiques au bit
    près (ratio 1.000 sur 3 mesures appariées).
    """
    m = _by_platform("5AC")
    assert m["dl_capacity_mbps"] == 102.7
    assert m["ul_capacity_mbps"] == 98.8
    assert m["dl_capacity_mbps"] > m["ul_capacity_mbps"]


def test_relative_labels_are_crossed():
    """`rx`/`tx` sont relatifs à qui répond → croisés côté AP.

    L'AP REÇOIT le montant : son `airmax.rx.cinr` est donc le CINR UL, et son
    `signal` est le niveau reçu de l'abonné. Sans ce croisement, DL et UL
    seraient silencieusement permutés sur tous les abonnés airMAX.
    """
    raw = _ap_status()
    sta = next(s for s in raw["wireless"]["sta"]
               if "5AC" in str((s.get("remote") or {}).get("platform")))
    m = _by_platform("5AC")

    assert m["cinr_db"] == sta["airmax"]["tx"]["cinr"]        # DL = tx de l'AP
    assert m["ul_cinr_db"] == sta["airmax"]["rx"]["cinr"]     # UL = rx de l'AP
    assert m["signal_dbm"] == sta["remote"]["signal"]         # DL = reçu au CPE
    assert m["remote_signal_dbm"] == sta["signal"]            # UL = reçu à l'AP


def test_throughput_comes_from_the_cpe_via_remote():
    """`remote.rx_throughput` = ce que l'abonné reçoit = DESCENDANT."""
    m = _by_platform("5AC")
    assert m["dl_throughput_mbps"] == 0.034     # 34 kb/s relayés par le CPE
    # Un débit reste sans commune mesure avec la capacité du même lien.
    assert m["dl_throughput_mbps"] < m["dl_capacity_mbps"] / 100


def test_airos6_station_has_capacity_but_no_throughput():
    """Le M5 (airOS 6) ne remonte AUCUN débit à son AP.

    Constaté sur 5 captures : ses `remote.*_throughput` restent à 0 alors que
    les 12 pairs airOS 8 rapportent du trafic à chaque fois, y compris quand le
    bloc `remote` du M5 est frais. On laisse donc la clé ABSENTE — publier le 0
    ferait passer un abonné actif pour un abonné muet.
    """
    m = _by_platform("M5")
    assert m["dl_throughput_mbps"] is None
    assert m["ul_throughput_mbps"] is None
    # En revanche l'AP calcule pour lui une VRAIE capacité en Mb/s, que le M5
    # est incapable de donner sur lui-même (il n'expose que son taux PHY).
    assert m["dl_capacity_mbps"] == 49.4
    assert m["ul_capacity_mbps"] == 32.24
    # ⚠️ Aucun compteur d'octets ici : la CONSOMMATION garde sa source
    # historique (le compteur du CPE via SSH). Le compteur que l'AP tient pour
    # une station est un cumul d'une autre origine — 55 Gio contre 2 Gio
    # mesurés sur le même client au même instant — et basculer de source ferait
    # facturer l'écart au client.
    assert m["radio_rx_bytes"] is None
    assert m["radio_tx_bytes"] is None


def test_airos6_station_reports_no_link_potential():
    """linkscore 0/0 sur airMAX-M → pas de Link Potential, et surtout pas 0 %.

    Un « potentiel du lien à 0 % » sur un abonné sain déclencherait une alerte
    et se tracerait comme une chute réelle.
    """
    assert _by_platform("M5")["link_potential_pct"] is None
    assert _by_platform("5AC")["link_potential_pct"] is not None


def test_empty_or_malformed_input_is_safe():
    assert parse_airos_ap_stations({}) == []
    assert parse_airos_ap_stations({"wireless": {}}) == []
    assert parse_airos_ap_stations({"wireless": {"sta": "nope"}}) == []
    assert parse_airos_ap_stations({"wireless": {"sta": [None, 42]}}) == []


def test_ap_never_sources_the_consumption_counters():
    """L'AP ne doit JAMAIS fournir les compteurs d'octets.

    La consommation est facturée : elle garde le compteur du CPE (SSH
    `wstalist`), sa source depuis toujours. Le compteur que l'AP tient pour une
    station est un cumul d'une AUTRE origine — mesuré sur un même client au même
    instant, l'AP annonçait 55,46 Gio de download quand le CPE en annonçait
    2,03. `consumption_service` somme des deltas `LAG()` : au premier cycle
    après une bascule de source, cet écart serait compté comme de la
    consommation réelle. Le plafond anti-glitch (8 Gio) n'en rattrape qu'une
    partie — un écart de 0 à 8 Gio passerait pour du trafic facturable.
    """
    for _mac, m, _meta in parse_airos_ap_stations(_ap_status()):
        assert m["radio_rx_bytes"] is None
        assert m["radio_tx_bytes"] is None


def test_airos6_station_gets_no_cinr_from_the_ap():
    """Le CINR d'une station airOS 6 ne doit PAS venir de l'AP.

    L'AP annonce 3 dB pour un M5 dont le SNR réel est de 25 dB. Publié, ce
    chiffre placerait TOUS les M5 sous le seuil critique de 10 dB → alertes
    massives et fausses. Leur CINR vient du SSH, mesuré au CPE.
    """
    assert _by_platform("M5")["cinr_db"] is None
    assert _by_platform("M5")["ul_cinr_db"] is None
    # Les stations airOS 8, elles, ont un CINR exploitable.
    assert _by_platform("5AC")["cinr_db"] is not None


def test_station_exposes_every_address_it_announces():
    """L'AP donne les adresses COURANTES de chaque abonné — c'est ce qui suit un roaming.

    `remote.ipaddr` est une LISTE côté airOS (une entrée par interface du CPE).
    Le parser ne doit PAS en élire une : elle mélange l'IP de management et le
    LAN du CPE (`192.168.10.1`, `172.16.0.1`… valeurs d'usine), dans un ordre
    non garanti. Le tri revient à `discovery_service.pick_management_ip`, sur le
    plan d'adressage configuré — choisir ici reviendrait à tirer au sort.
    """
    stations = parse_airos_ap_stations(_ap_status())
    ips = {tuple(meta["mgmt_ips"]) for _mac, _m, meta in stations}
    assert ips == {("10.0.0.11",), ("10.0.0.12",)}


def test_missing_or_scalar_address_list_is_tolerated():
    """Une station sans `ipaddr`, ou qui l'annonce en scalaire, ne casse rien.

    Liste vide = la réconciliation identifie la station par sa MAC seule et
    laisse son IP inchangée — jamais une exception qui ferait sauter le cycle
    de tout l'AP.
    """
    raw = _ap_status()
    del raw["wireless"]["sta"][0]["remote"]["ipaddr"]
    raw["wireless"]["sta"][1]["remote"]["ipaddr"] = "10.0.0.99"
    metas = [meta for _mac, _m, meta in parse_airos_ap_stations(raw)]
    assert metas[0]["mgmt_ips"] == []
    assert metas[1]["mgmt_ips"] == ["10.0.0.99"]
