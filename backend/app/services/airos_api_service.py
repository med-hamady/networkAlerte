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
  tx_rate_mbps / rx_rate_mbps   : airmax.dl_capacity / ul_capacity (Kbps→Mbps)
  tx_ideal_mbps / rx_ideal_mbps : dl_capacity_expect / ul_capacity_expect (Kbps→Mbps)
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


def _kbps_to_mbps(val: object) -> float | None:
    """Convert a Kbps value to Mbps (rounded), or None."""
    f = _float(val)
    return round(f / 1000.0, 2) if f is not None else None


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
    result: dict[str, float | None] = {
        "signal_dbm":          None,
        "cinr_db":             None,
        "ul_cinr_db":          None,
        "tx_rate_mbps":        None,
        "rx_rate_mbps":        None,
        "tx_ideal_mbps":       None,
        "rx_ideal_mbps":       None,
        "total_capacity_mbps": None,
        "link_potential_pct":  None,
        "local_rx_rate_idx":   None,
        "remote_rx_rate_idx":  None,
        "remote_signal_dbm":   None,
        "distance_m":          None,
        "radio_rx_bytes":      None,
        "radio_tx_bytes":      None,
        "uptime_seconds":      None,
    }

    result["uptime_seconds"] = _float(_nested(raw, "host", "uptime"))

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
    result["tx_ideal_mbps"] = _kbps_to_mbps(sta.get("dl_capacity_expect"))
    result["rx_ideal_mbps"] = _kbps_to_mbps(sta.get("ul_capacity_expect"))
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
        result["tx_rate_mbps"] = _kbps_to_mbps(airmax.get("dl_capacity"))
        result["rx_rate_mbps"] = _kbps_to_mbps(airmax.get("ul_capacity"))
        result["total_capacity_mbps"] = _kbps_to_mbps(airmax.get("cb_capacity"))

    # Fallback Total Capacity from the radio-wide polling block.
    if result["total_capacity_mbps"] is None:
        result["total_capacity_mbps"] = _kbps_to_mbps(
            _nested(raw, "wireless", "polling", "cb_capacity")
        )

    return result


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
