"""
airOS HTTP API service — collects link-quality metrics from airMAX LR (LiteBeam)
devices via the device's own airOS web API. Replaces SNMP for airMAX LRs: the
UBNT Enterprise MIB does not expose the composite Link Potential / Total Capacity
values shown on the airOS dashboard, but ``/status.cgi`` does.

Authentication : classic airOS cookie auth (NOT the LTU x-auth-token flow):
  GET  https://{ip}/login.cgi                      → warm session cookie
  POST https://{ip}/login.cgi  (form-encoded)      → AIROS_<mac> session cookie (302)
  GET  https://{ip}/status.cgi                     → JSON status (cookie carried)

Metrics extracted from ``wireless.sta[0]`` (the AP this station is linked to) and
``host`` — emitted under the SAME keys as ltu_api_service so the alert engine,
DeviceMetric persistence and the frontend modal all work unchanged:
  link_potential_pct  : mean(dl_linkscore, ul_linkscore) — UI "Link Potential"
  total_capacity_mbps : airmax.cb_capacity (Kbps→Mbps)   — UI "Total Capacity"
  dl_capacity_mbps / ul_capacity_mbps : airmax.dl_capacity / ul_capacity
      (Kbps→Mbps) — what the link COULD carry. UI "Capacity RX".
  dl_throughput_mbps / ul_throughput_mbps : wireless.throughput.rx / .tx
      (Kbps→Mbps) — what actually flows. UI "Throughput RX".

      ⚠ Direction is inverted vs the key names, and the block sits OUTSIDE
      ``wireless.sta[0]``. We poll the CPE (station), so its RX is the
      customer's DOWNLINK and its TX the UPLINK — hence rx→dl and tx→ul.
      Confirmed on a live LiteBeam 5AC (fw v8.7.22, 2026-07-20): capacity
      145080 Kbps alongside throughput.rx 186 Kbps — three orders of magnitude
      apart. Never derive one from the other.
  local_rx_rate_idx   : rx_idx (AP→CPE downlink "Nx") — UI "Local RX Data Rate"
  remote_rx_rate_idx  : tx_idx (CPE→AP uplink "Nx")
  signal_dbm          : station signal from AP (dBm)
  cinr_db / ul_cinr_db: airmax.rx.cinr / airmax.tx.cinr (dB)
  remote_signal_dbm   : remote.signal — AP-side signal (dBm)
  distance_m          : link distance (m)
  radio_rx_bytes / radio_tx_bytes : stats.rx_bytes / stats.tx_bytes (cumulative)
  uptime_seconds      : host.uptime (s)

Note: airOS AC has no CCQ over HTTP (link score replaces it), so ccq_pct is not
emitted — link quality on airMAX is carried by link_potential_pct.
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _float(val: object) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _nested(obj: object, *keys: str) -> object:
    """Walk a nested dict by key path; return None if any key is missing."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
        if obj is None:
            return None
    return obj


# Clés produites par les DEUX parsers de lien (côté CPE et côté AP). Partagées
# pour que la fiche, la persistance et l'alerting soient indifférents à la
# source : basculer un LR du poll direct au poll par son AP ne doit rien changer
# en aval.
_LINK_METRIC_KEYS: tuple[str, ...] = (
    "signal_dbm",
    "cinr_db",
    "ul_cinr_db",
    "dl_capacity_mbps",
    "ul_capacity_mbps",
    "dl_throughput_mbps",
    "ul_throughput_mbps",
    "total_capacity_mbps",
    "link_potential_pct",
    "local_rx_rate_idx",
    "remote_rx_rate_idx",
    "remote_signal_dbm",
    "distance_m",
    "radio_rx_bytes",
    "radio_tx_bytes",
    "uptime_seconds",
)


def _kbps_to_mbps(val: object, ndigits: int = 2) -> float | None:
    """Convert a Kbps value to Mbps (rounded), or None.

    ``ndigits`` defaults to 2 for capacities (hundreds of Mbps); throughput is
    read at 3 so a sub-Mbps idle link keeps a usable value instead of 0.0.
    """
    f = _float(val)
    return round(f / 1000.0, ndigits) if f is not None else None


class AirOsApiClient:
    """HTTPS client for the airOS web API (login.cgi cookie auth + status.cgi)."""

    def __init__(self, host: str, username: str, password: str, port: int = 443):
        self._base = f"https://{host}:{port}"
        self._username = username
        self._password = password

    async def fetch_status(self) -> dict | None:
        """
        Authenticate via login.cgi (cookie session), then GET /status.cgi.
        Returns the raw JSON dict or None on failure/unreachable.

        The password may contain '@' — httpx form-encodes it to %40, which
        airOS login.cgi decodes correctly (confirmed on fw v8.7.22).
        """
        try:
            # LAN devices answer in <2 s; tight timeouts keep slow/half-open
            # airOS from hogging a concurrency slot (a hung device must not stall
            # the whole poll — the job deadline + these per-request caps bound it).
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(4.0, connect=3.0),
                verify=get_settings().tls_verify_devices,
            ) as client:
                # Warm the session (airOS sets an initial cookie on the GET).
                await client.get(f"{self._base}/login.cgi", timeout=3)
                login = await client.post(
                    f"{self._base}/login.cgi",
                    data={"username": self._username, "password": self._password},
                    timeout=4,
                )
                # 302 = redirect to dashboard on success; some firmwares return 200.
                if login.status_code not in (200, 302):
                    logger.warning(
                        "airOS login failed (%s): HTTP %d", self._base, login.status_code
                    )
                    return None
                resp = await client.get(f"{self._base}/status.cgi", timeout=4)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.debug(
                        "airOS API (%s) status.cgi → keys=%s",
                        self._base,
                        list(data.keys()) if isinstance(data, dict) else type(data),
                    )
                    return data if isinstance(data, dict) else None
                logger.warning(
                    "airOS status.cgi failed (%s): HTTP %d", self._base, resp.status_code
                )
        except httpx.RequestError as exc:
            logger.debug("airOS API unreachable (%s): %s", self._base, exc)
        except Exception as exc:  # noqa: BLE001 — never let a poll crash the scheduler
            logger.error("airOS API unexpected error (%s): %s", self._base, exc)
        return None


def parse_airos_link_metrics(raw: dict) -> dict[str, float | None]:
    """Map airOS status.cgi (``wireless.sta[0]`` + ``host``) to LTU metric keys.

    Only keys actually present are filled; everything else stays None. Returns
    an all-None dict when there is no connected station.
    """
    result: dict[str, float | None] = dict.fromkeys(_LINK_METRIC_KEYS)

    result["uptime_seconds"] = _float(_nested(raw, "host", "uptime"))

    # THROUGHPUT — radio-level block, outside `sta`. On a CPE the station list
    # holds a single link, so the radio totals ARE that link's traffic.
    # rx = what the CPE receives = customer downlink; tx = uplink.
    result["dl_throughput_mbps"] = _kbps_to_mbps(
        _nested(raw, "wireless", "throughput", "rx"), ndigits=3
    )
    result["ul_throughput_mbps"] = _kbps_to_mbps(
        _nested(raw, "wireless", "throughput", "tx"), ndigits=3
    )

    sta_list = _nested(raw, "wireless", "sta")
    if not isinstance(sta_list, list) or not sta_list:
        return result
    sta = sta_list[0]
    if not isinstance(sta, dict):
        return result

    result["signal_dbm"]  = _float(sta.get("signal"))
    result["distance_m"]  = _float(sta.get("distance"))
    result["local_rx_rate_idx"]  = _float(sta.get("rx_idx"))
    result["remote_rx_rate_idx"] = _float(sta.get("tx_idx"))
    result["remote_signal_dbm"] = _float(_nested(sta, "remote", "signal"))
    result["radio_rx_bytes"] = _float(_nested(sta, "stats", "rx_bytes"))
    result["radio_tx_bytes"] = _float(_nested(sta, "stats", "tx_bytes"))

    # Link Potential = mean of DL/UL link score (matches dashboard, same as LTU).
    dl_score = _float(sta.get("dl_linkscore"))
    ul_score = _float(sta.get("ul_linkscore"))
    if dl_score is None:
        dl_score = _float(sta.get("dl_avg_linkscore"))
    if ul_score is None:
        ul_score = _float(sta.get("ul_avg_linkscore"))
    if dl_score is not None and ul_score is not None:
        result["link_potential_pct"] = round((dl_score + ul_score) / 2.0, 1)
    elif dl_score is not None:
        result["link_potential_pct"] = round(dl_score, 1)

    airmax = sta.get("airmax")
    if isinstance(airmax, dict):
        result["cinr_db"]    = _float(_nested(airmax, "rx", "cinr"))
        result["ul_cinr_db"] = _float(_nested(airmax, "tx", "cinr"))
        result["dl_capacity_mbps"] = _kbps_to_mbps(airmax.get("dl_capacity"))
        result["ul_capacity_mbps"] = _kbps_to_mbps(airmax.get("ul_capacity"))
        result["total_capacity_mbps"] = _kbps_to_mbps(airmax.get("cb_capacity"))

    # Fallback Total Capacity from the radio-wide polling block.
    if result["total_capacity_mbps"] is None:
        result["total_capacity_mbps"] = _kbps_to_mbps(
            _nested(raw, "wireless", "polling", "cb_capacity")
        )

    return result


def _normalize_mac(value: object) -> str | None:
    """Lowercase colon-separated MAC, or None. Same identity key as the LTU
    fan-out and `discovery_service` — a station is bound to its LR by MAC."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _ip_list(value: object) -> list[str]:
    """Toutes les adresses annoncées par la station (`remote.ipaddr`).

    airOS rend une LISTE (une entrée par interface du CPE), d'où le déballage.
    Elle contient l'IP de management — la SEULE source qui suive un client quand
    il roame d'un AP à l'autre — mais AUSSI le LAN du CPE (`192.168.10.1`,
    `172.16.0.1`… valeurs d'usine), dans un ordre non garanti.

    On rend donc la liste ENTIÈRE sans en élire une : c'est
    `discovery_service.pick_management_ip` qui tranche, sur le plan
    d'adressage configuré. Choisir ici reviendrait à tirer au sort — et une IP
    LAN écrite en base vole sa ligne à un autre client (cf. `is_management_ip`).
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def parse_airos_ap_stations(raw: dict) -> list[tuple[str | None, dict, dict]]:
    """Per-station metrics read from an airMAX **AP** (`wireless.sta[]`).

    One HTTP call to a Rocket returns every connected client, so a site with 14
    subscribers costs 1 login+status instead of 14 — the same fan-out the LTU
    poll already does. Returns ``(mac, metrics)`` per station, metrics carrying
    the SAME keys as :func:`parse_airos_link_metrics` so persistence, the alert
    engine and the modal need no change.

    ⚠️ **Two label families, and only one of them flips.** Verified on a live
    ap-ptmp Rocket (fw v8.7.22) against one of its CPEs polled back-to-back,
    3 paired samples:

    * ``dl_*`` / ``ul_*`` are **absolute** — ``airmax.dl_capacity`` and
      ``dl_linkscore`` came back byte-identical from both ends (ratio 1.000).
      Read them as-is.
    * ``rx`` / ``tx`` are **relative to whoever answers** and MUST be crossed.
      ``AP.airmax.rx.cinr == CPE.airmax.tx.cinr`` on 3/3 samples. The AP
      *receives* the uplink, so its ``rx`` is the customer's UPLINK — the
      opposite of the CPE-side parser.

    Getting that backwards would silently swap DL and UL on every airMAX
    subscriber, so each crossed field is commented individually below.

    ⚠️ **airOS 6 stations (LiteBeam M5) report no throughput**: their
    ``remote.rx_throughput``/``tx_throughput`` stay 0 — confirmed over 5
    captures while all 12 airOS 8 peers reported traffic every time, including
    when the M5's ``remote`` block was fresh (age 1-4 s). Their ``linkscore``
    is 0 too (no Link Potential on airMAX-M). We therefore leave throughput
    ABSENT for them rather than publishing a fake 0 — the byte counters are
    still exported so the caller can derive it.
    """
    sta_list = _nested(raw, "wireless", "sta")
    if not isinstance(sta_list, list):
        return []

    stations: list[tuple[str | None, dict, dict]] = []
    for sta in sta_list:
        if not isinstance(sta, dict):
            continue
        m: dict[str, float | None] = dict.fromkeys(_LINK_METRIC_KEYS)
        remote = sta.get("remote") if isinstance(sta.get("remote"), dict) else {}

        # --- champs ABSOLUS : lus tels quels ---------------------------------
        m["distance_m"] = _float(sta.get("distance"))
        dl_score = _float(sta.get("dl_linkscore"))
        ul_score = _float(sta.get("ul_linkscore"))
        if dl_score is None:
            dl_score = _float(sta.get("dl_avg_linkscore"))
        if ul_score is None:
            ul_score = _float(sta.get("ul_avg_linkscore"))
        # linkscore 0/0 = station airOS-M : pas de Link Potential, on laisse None
        # plutôt que d'afficher un lien « à 0 % » sur un client sain.
        if dl_score and ul_score:
            m["link_potential_pct"] = round((dl_score + ul_score) / 2.0, 1)
        elif dl_score:
            m["link_potential_pct"] = round(dl_score, 1)

        airmax = sta.get("airmax")
        if isinstance(airmax, dict):
            m["dl_capacity_mbps"] = _kbps_to_mbps(airmax.get("dl_capacity"))
            m["ul_capacity_mbps"] = _kbps_to_mbps(airmax.get("ul_capacity"))
            m["total_capacity_mbps"] = _kbps_to_mbps(airmax.get("cb_capacity"))
            # CINR : seulement pour les stations airOS 8. Sur une station
            # airOS 6 (M5), l'AP annonce 3 dB là où le SNR réel est de 25 —
            # publier ça ferait passer TOUS les M5 sous le seuil critique de
            # 10 dB. Le linkscore à 0 est le marqueur de ces stations (elles
            # n'ont pas la notion) : il signale un bloc `airmax` inexploitable.
            # Leur CINR vient du SSH `wstalist`, mesuré au CPE.
            # CROISÉ : l'AP reçoit le montant, donc son rx.cinr est l'UL.
            if dl_score or ul_score:
                m["cinr_db"]    = _float(_nested(airmax, "tx", "cinr"))
                m["ul_cinr_db"] = _float(_nested(airmax, "rx", "cinr"))

        # --- champs RELATIFS : croisés ---------------------------------------
        # `signal` = ce que l'AP reçoit (montant) ; `remote.signal` = ce que le
        # CPE reçoit (descendant), donc c'est lui qui porte la sémantique que le
        # parser CPE appelle `signal_dbm`.
        m["signal_dbm"]        = _float(remote.get("signal"))
        m["remote_signal_dbm"] = _float(sta.get("signal"))
        # `rx_idx` = index de modulation en réception de l'AP = montant.
        m["local_rx_rate_idx"]  = _float(sta.get("tx_idx"))
        m["remote_rx_rate_idx"] = _float(sta.get("rx_idx"))

        # DÉBIT : mesuré par le CPE et relayé. `remote.rx_throughput` = ce que
        # le client reçoit = DESCENDANT. 0 sur airOS 6 → laissé absent.
        dl_kbps = _float(remote.get("rx_throughput"))
        ul_kbps = _float(remote.get("tx_throughput"))
        if dl_kbps:
            m["dl_throughput_mbps"] = round(dl_kbps / 1000.0, 3)
        if ul_kbps:
            m["ul_throughput_mbps"] = round(ul_kbps / 1000.0, 3)

        # ⚠️ PAS DE COMPTEURS D'OCTETS ICI — volontaire.
        # Le compteur de l'AP pour une station et le compteur propre du CPE
        # sont deux cumuls d'ORIGINES DIFFÉRENTES : mesuré sur le même client au
        # même instant, l'AP annonçait 55,46 Gio de download quand le CPE en
        # annonçait 2,03. `consumption_service` somme des deltas `LAG()` ; si la
        # source changeait, le premier delta après bascule vaudrait cet écart et
        # serait FACTURÉ comme de la consommation réelle. Le plafond
        # anti-glitch (8 Gio) n'en écarte qu'une partie — un écart de 0 à 8 Gio
        # passerait inaperçu.
        # La conso reste donc lue sur le compteur du CPE (SSH `wstalist`), qui
        # ne change pas d'origine. Cf. ssh_service._parse_wstalist_metrics.

        m["uptime_seconds"] = _float(sta.get("uptime"))

        # Champs NON numériques que l'AP offre gratuitement sur chaque abonné et
        # que le poll direct allait chercher un par un : nom configuré, mode
        # routeur/bridge (garde-fou du blocage client) et modèle réel.
        hostname = remote.get("hostname")
        meta = {
            "hostname": hostname.strip() if isinstance(hostname, str) and hostname.strip() else None,
            "netrole": remote.get("netrole"),
            "platform": remote.get("platform"),
            # Adresses courantes de la station, telles que les voit l'AP qui la
            # sert MAINTENANT — servent à la réconciliation (cf. appelant). Non
            # filtrées ici : le tri est fait par `discovery_service`.
            "mgmt_ips": _ip_list(remote.get("ipaddr")),
        }
        stations.append((_normalize_mac(sta.get("mac")), m, meta))

    return stations


def _extract_hostname(raw: dict) -> str | None:
    """airOS-configured device name (host.hostname), used for LR auto-rename."""
    name = _nested(raw, "host", "hostname")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _extract_model(raw: dict) -> str | None:
    """airOS-reported hardware model (``host.devmodel``), e.g. "LBE-M5-23",
    "LiteBeam 5AC Gen2". Falls back to ``host.model``. Used to auto-correct the
    LR ``model_variant`` (M5 vs 5AC) from the device itself — airOS is the source
    of truth for the actual hardware, so a peer/UISP misclassification at creation
    gets fixed on the next poll."""
    for key in ("devmodel", "model"):
        val = _nested(raw, "host", key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def airmax_variant_from_model(model: str | None) -> str | None:
    """Resolve an airMAX LiteBeam ``model_variant`` from an airOS model string.

    Returns ``"litebeam_m5"`` or ``"litebeam_5ac"`` when the string is
    unambiguous, else ``None`` (leave the current variant untouched). This is
    only ever applied to devices already known to be airMAX LiteBeams, so the
    result is deliberately restricted to those two values — it can never flip an
    LR out of the airMAX family (which would drop it from the airOS poll).
    """
    if not model:
        return None
    norm = model.lower()
    if "m5" in norm:
        return "litebeam_m5"
    if "5ac" in norm or "ac" in norm:
        return "litebeam_5ac"
    return None


def _extract_netrole(raw: dict) -> str | None:
    """Router vs bridge mode (host.netrole). airOS reports "router"/"bridge".

    Returns the normalized value when it is one of those two (the topology /
    client-block logic only acts on a confirmed state), else None so an
    unexpected value never erases a previously-known classification.
    """
    role = _nested(raw, "host", "netrole")
    if isinstance(role, str):
        role = role.strip().lower()
        if role in ("router", "bridge"):
            return role
    return None


def parse_airos_channel_width_mhz(raw: dict) -> float | None:
    """Channel width (MHz) from airOS status.cgi.

    airMAX exposes it as ``wireless.chanbw`` (e.g. 10/20/40). The newer
    ``wireless.chwidth`` key is None on the Rocket firmware seen in the field,
    so ``chanbw`` is the source of truth. Used by the rocket_client_overload
    rule to pick the per-width client ceiling for airMAX base stations.
    """
    return _float(_nested(raw, "wireless", "chanbw"))


async def collect_airos_channel_width(
    host: str, username: str, password: str, port: int = 443
) -> float | None:
    """Fetch airOS status and return the radio channel width in MHz, or None
    when the device is unreachable / auth fails / the field is absent."""
    raw = await AirOsApiClient(host, username, password, port).fetch_status()
    if raw is None:
        return None
    return parse_airos_channel_width_mhz(raw)


async def collect_airos_link_metrics(
    host: str, username: str, password: str, port: int = 443
) -> tuple[dict[str, float | None], str | None, str | None, str | None] | None:
    """Fetch + parse airOS status. Returns (metrics, hostname, netrole,
    model_variant) or None if the device is unreachable / auth fails.
    ``netrole`` is "router", "bridge", or None (unknown). ``model_variant`` is
    the airMAX variant resolved from ``host.devmodel`` ("litebeam_m5" /
    "litebeam_5ac"), or None when the model string is absent/ambiguous."""
    raw = await AirOsApiClient(host, username, password, port).fetch_status()
    if raw is None:
        return None
    return (
        parse_airos_link_metrics(raw),
        _extract_hostname(raw),
        _extract_netrole(raw),
        airmax_variant_from_model(_extract_model(raw)),
    )
