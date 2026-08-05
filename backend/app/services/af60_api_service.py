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


# Interface radio de l'AF60 dans ``interfaces[]`` (les autres : eth0, le bond
# ubond0 et le bridge br0). C'est la SEULE qui mesure le lien 60 GHz lui-même.
_RADIO_IFACE_ID = "wlan0"


def _set_throughput(raw: dict, result: dict[str, float | None]) -> None:
    """Renseigne ``dl/ul_throughput_mbps`` depuis les compteurs de ``wlan0``.

    ⚠ **Pas** depuis ``peers[0].common.counters`` (ce que faisait le code
    précédent, recopié du LTU sans vérification). Mesuré le 2026-08-03 sur un
    AF60-LR réel (10.135.80.1, fw v2.6.8), 6 relevés à 10 s d'intervalle : ce
    bloc-là est **relayé par la radio et retarde**, au point d'annoncer
    **0,06 Mb/s pendant que le lien écoulait 76,69 Mb/s**. Une courbe bâtie
    dessus montrerait des effondrements qui n'ont pas eu lieu. Les compteurs de
    ``wlan0``, eux, collent au bit près à ceux d'``eth0`` à chaque relevé
    (76,69 / 76,68 — le trafic qui entre par la radio ressort par le cuivre),
    donc ils sont la mesure locale et directe.

    **Unité = bit/s**, vérifiée par le contrôle taille de paquet, comme pour le
    LTU : ``rxRate 66_100_104 ÷ 8 ÷ rxPPS 6520 = 1267 octets/paquet`` — sous la
    MTU, ce qui n'est vrai que si la valeur est en bits.

    ⚠ **SENS** — ``rx``/``tx`` sont relatifs à l'équipement interrogé, il faut
    donc les rattacher au ``dl``/``ul`` que le firmware emploie lui-même dans
    ``linkQuality.capacity`` (déjà la source de ``dl/ul_capacity_mbps``), sinon
    la fiche afficherait un débit descendant sous une capacité montante. Sur ce
    lien le firmware annonce ``capacity.dl=600000`` / ``ul=975000`` avec
    ``mcs.rxIdx=6`` / ``txIdx=9`` : la direction la moins bien modulée est aussi
    la moins capable, donc **dl = ce que l'équipement REÇOIT (rx)** et
    **ul = ce qu'il ÉMET (tx)**. ``linkScore`` (dl 26 / ul 43) concorde.

    ``wlan0`` absent (firmware inattendu) ⇒ les deux clés restent None : un trou
    dans la courbe, jamais un 0 ni une valeur reprise d'une source douteuse.
    """
    stats = None
    for iface in raw.get("interfaces") or []:
        if isinstance(iface, dict) and iface.get("id") == _RADIO_IFACE_ID:
            stats = iface.get("statistics")
            break
    if not isinstance(stats, dict):
        logger.debug("AF60 : interface %s absente — débit non mesuré", _RADIO_IFACE_ID)
        return

    rx_bps = _float(stats.get("rxRate"))
    tx_bps = _float(stats.get("txRate"))
    if rx_bps is not None:
        result["dl_throughput_mbps"] = round(rx_bps / 1_000_000.0, 3)
    if tx_bps is not None:
        result["ul_throughput_mbps"] = round(tx_bps / 1_000_000.0, 3)


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

    # DÉBIT réel (trafic écoulé) — une valeur INSTANTANÉE par relevé, à ne pas
    # confondre avec la capacité plus bas.
    _set_throughput(raw, result)

    peer = _nested(raw, "wireless", "peers", 0)
    if not isinstance(peer, dict):
        return result

    result["distance_m"] = _float(_nested(peer, "common", "distance"))

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
    host: str,
    username: str,
    password: str,
    port: int = 443,
    *,
    fallback_passwords: list[str] | None = None,
) -> tuple[dict[str, float | None], str] | None:
    """Authentifie + récupère + parse les statistiques AF60.

    Essaie le ``password`` stocké puis, s'il échoue, chaque ``fallback_passwords``
    dans l'ordre (plusieurs AF60 ont été flashés avec une variante du mot de passe
    de convention). S'arrête au premier qui authentifie ET rend des données de
    lien.

    Retourne ``(métriques, mot_de_passe_qui_a_marché)`` — l'appelant compare ce
    mot de passe au stocké pour promouvoir un repli sur la fiche. Retourne None si
    l'équipement est injoignable ou si AUCUN mot de passe n'authentifie (poll raté,
    sans alerte ici). Le coût des replis ne tombe QUE sur les AF60 dont le mot de
    passe stocké échoue (un AF60 sain auth au 1er essai)."""
    candidates = [password, *(fallback_passwords or [])]
    for idx, candidate in enumerate(candidates):
        raw = await LTUApiClient(host, username, candidate, port).fetch_stats()
        if raw is None:
            # Auth refusée OU injoignable — on ne distingue pas ici : on tente le
            # repli suivant. Un AF60 vraiment injoignable épuisera la liste (borné).
            continue
        metrics = parse_af60_metrics(raw)
        # af60_link_up est toujours défini ; on vérifie qu'au moins une métrique de
        # lien a été lue (sinon réponse vide/inattendue). Ici l'auth a RÉUSSI, donc
        # c'est un souci device et pas de mot de passe → on ne tente pas d'autre pw.
        if all(v is None for k, v in metrics.items() if k != "af60_link_up"):
            logger.warning("AF60 API : réponse sans données de lien (%s)", host)
            return None
        if idx > 0:
            logger.info(
                "AF60 %s — mot de passe de repli #%d accepté (à promouvoir)", host, idx
            )
        return metrics, candidate
    return None
