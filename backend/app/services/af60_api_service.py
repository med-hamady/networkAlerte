"""
airFiber 60 (AF60-LR) HTTP API service — collecte les métriques de lien 60 GHz
via l'API locale UDAPI de l'équipement.

L'AF60 parle EXACTEMENT la même UDAPI que les LTU :
  POST https://{ip}/api/auth (form username/password)  → JSON { utoken: "..." }
  GET  https://{ip}/api/v1.0/statistics  (header x-auth-token: <utoken>)

On réutilise donc ``ltu_api_service.LTUApiClient`` pour l'auth + le fetch (qui
déballe déjà la réponse liste ``[{...}]``), et on ne fournit ici que le parsing
spécifique AF60. Différences vs LTU : le 60 GHz expose ``snr`` (et pas ``cinr``),
et pas de ``capacity.combined`` (on somme dl+ul). Lien point-à-point : un seul
peer dans ``wireless.peers[0]`` (l'autre extrémité du backhaul).

Mapping confirmé terrain le 2026-06-05 sur 10.135.80.1 — voir mémoire
reference_af60_local_api.md.
"""

import logging

from app.services.ltu_api_service import LTUApiClient

logger = logging.getLogger(__name__)


# Unités des métriques AF60 (consommées par le job pour DeviceMetric.unit et par
# l'endpoint metrics/live). af60_link_up est un booléen (1.0/0.0), sans unité.
METRIC_UNITS: dict[str, str] = {
    "af60_link_up":        "",
    "signal_dbm":          "dBm",
    "snr_db":              "dB",
    "remote_signal_dbm":   "dBm",
    "remote_snr_db":       "dB",
    "link_potential_pct":  "%",
    "total_capacity_mbps": "Mbps",
    "dl_capacity_mbps":    "Mbps",
    "ul_capacity_mbps":    "Mbps",
    "dl_throughput_mbps":  "Mbps",
    "ul_throughput_mbps":  "Mbps",
    "local_rx_rate_idx":   "x",
    "remote_rx_rate_idx":  "x",
    "distance_m":          "m",
    "uptime_seconds":      "s",
    "cpu_pct":             "%",
    "ram_pct":             "%",
}


def _float(val: object) -> float | None:
    """Convertit en float, ou None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _nested(obj: object, *keys: object) -> object:
    """Parcourt un dict/list imbriqué par clés/index ; None si une étape manque."""
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int):
            obj = obj[key] if -len(obj) <= key < len(obj) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def _kbps_to_mbps(val: object) -> float | None:
    f = _float(val)
    return round(f / 1000.0, 2) if f is not None else None


def parse_af60_metrics(raw: dict) -> dict[str, float | None]:
    """Mappe ``/api/v1.0/statistics`` (déjà déballé) vers nos clés de métriques.

    Toutes les clés sont présentes ; celles absentes côté device restent None.
    ``af60_link_up`` vaut toujours 0.0/1.0 (jamais None) pour que la règle
    « lien coupé » s'évalue même si le bloc wireless est vide.
    """
    result: dict[str, float | None] = dict.fromkeys(METRIC_UNITS)

    # État du lien radio (radios[0].linkState) — binaire, toujours défini.
    link_state = _nested(raw, "wireless", "radios", 0, "linkState")
    result["af60_link_up"] = 1.0 if link_state == "connected" else 0.0

    # Santé device (gratuit dans la même réponse).
    result["uptime_seconds"] = _float(_nested(raw, "device", "uptime"))
    result["cpu_pct"] = _float(_nested(raw, "device", "cpu", 0, "usage"))
    result["ram_pct"] = _float(_nested(raw, "device", "ram", "usage"))

    peer = _nested(raw, "wireless", "peers", 0)
    if not isinstance(peer, dict):
        return result

    result["distance_m"] = _float(_nested(peer, "common", "distance"))

    # DÉBIT réel (trafic écoulé), à distinguer de la capacité plus bas. Même
    # UDAPI que le LTU : compteurs `common.counters`, clés `txRate`/`rxRate`,
    # unité **bits par seconde** (vérifié sur un lien LTU, cf. ltu_api_service).
    # ⚠ NON VÉRIFIÉ sur un AF60 physique — le firmware 60 GHz peut ne pas
    # exposer ce bloc. Les clés restent alors à None plutôt que de valoir 0.
    counters = _nested(peer, "common", "counters")
    if isinstance(counters, dict):
        tx_bps = _float(counters.get("txRate"))
        rx_bps = _float(counters.get("rxRate"))
        if tx_bps is not None:
            result["dl_throughput_mbps"] = round(tx_bps / 1_000_000.0, 3)
        if rx_bps is not None:
            result["ul_throughput_mbps"] = round(rx_bps / 1_000_000.0, 3)

    lq = _nested(peer, "local", 0, "linkQuality")
    if isinstance(lq, dict):
        result["signal_dbm"] = _float(lq.get("signal"))
        result["snr_db"] = _float(lq.get("snr"))          # 60 GHz : SNR, pas CINR
        result["dl_capacity_mbps"] = _kbps_to_mbps(_nested(lq, "capacity", "dl"))
        result["ul_capacity_mbps"] = _kbps_to_mbps(_nested(lq, "capacity", "ul"))
        # Capacité totale = dl + ul (pas de capacity.combined sur l'AF60).
        dl = _float(_nested(lq, "capacity", "dl"))
        ul = _float(_nested(lq, "capacity", "ul"))
        if dl is not None and ul is not None:
            result["total_capacity_mbps"] = round((dl + ul) / 1000.0, 2)
        # Potentiel du lien = moyenne des linkScore DL/UL (comme LTU/airMAX).
        sd = _float(_nested(lq, "linkScore", "dl"))
        su = _float(_nested(lq, "linkScore", "ul"))
        if sd is not None and su is not None:
            result["link_potential_pct"] = round((sd + su) / 2.0, 1)
        elif sd is not None:
            result["link_potential_pct"] = round(sd, 1)
        result["local_rx_rate_idx"] = _float(_nested(lq, "mcs", "txRate"))
        result["remote_rx_rate_idx"] = _float(_nested(lq, "mcs", "rxRate"))

    rlq = _nested(peer, "remote", 0, "linkQuality")
    if isinstance(rlq, dict):
        result["remote_signal_dbm"] = _float(rlq.get("signal"))
        result["remote_snr_db"] = _float(rlq.get("snr"))

    return result


async def collect_af60_metrics(
    host: str, username: str, password: str, port: int = 443
) -> dict[str, float | None] | None:
    """Authentifie + récupère + parse les statistiques AF60.

    Retourne le dict de métriques, ou None si l'équipement est injoignable /
    l'auth échoue (l'appelant le traite comme un poll raté, sans alerte ici)."""
    raw = await LTUApiClient(host, username, password, port).fetch_stats()
    if raw is None:
        return None
    metrics = parse_af60_metrics(raw)
    # af60_link_up est toujours défini ; on vérifie qu'au moins une métrique de
    # lien a été lue (sinon réponse vide/inattendue → traiter comme injoignable).
    if all(v is None for k, v in metrics.items() if k != "af60_link_up"):
        logger.warning("AF60 API : réponse sans données de lien (%s)", host)
        return None
    return metrics
