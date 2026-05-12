"""
LTU HTTP API service — collects radio quality metrics via the device's local REST API.

Authentication : POST https://{ip}/api/auth  (form-encoded)  →  JSON body { utoken: "..." }
Stats endpoint : GET  https://{ip}/api/v1.0/statistics       →  x-auth-token: <utoken>

Metrics extracted from wireless.peers[0] and wireless.radios[0]:
  signal_dbm        : uplink signal at AP from CPE (dBm)
  noise_dbm         : noise floor at AP (dBm)
  ccq_pct / ul_ccq_pct : DL/UL link score 0–100 (LTU equivalent of CCQ)
  cinr_db / ul_cinr_db  : DL/UL CINR (dB)
  tx_rate_mbps / rx_rate_mbps : actual DL/UL capacity (Kbps → Mbps)
  tx_ideal_mbps / rx_ideal_mbps : ideal (uncapped) DL/UL capacity
  remote_signal_dbm : downlink signal at CPE from AP (dBm)
  remote_noise_dbm  : noise floor at CPE (dBm)
  remote_eirp_dbm   : AP transmit power EIRP (dBm)
  distance_m        : link distance (m)
  peer_uptime_s     : CPE uptime (s)
  peer_cpu_pct / peer_ram_pct : CPE CPU / RAM usage (%)
  peer_tx_kbps / peer_rx_kbps : CPE throughput counters (Kbps)
"""

import logging
from urllib.parse import quote as _urlquote

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LTUApiClient:
    """HTTPS client for the LTU local REST API (UDAPI v1.0)."""

    def __init__(self, host: str, username: str, password: str, port: int = 443):
        self._base = f"https://{host}:{port}"
        self._username = username
        self._password = password

    async def _login(self, client: httpx.AsyncClient) -> str | None:
        """
        POST /api/auth with form-encoded credentials.
        Returns the utoken string on success, None on failure.
        The utoken is then sent as x-auth-token header on subsequent requests.

        Note: body is sent as a raw string because AirOS firmware does not decode
        percent-encoded '@' (%40). We URL-encode everything else to handle special
        characters like '&', '=' or '%' in credentials, but leave '@' intact.
        """
        try:
            raw_body = (
                f"username={_urlquote(self._username, safe='@')}"
                f"&password={_urlquote(self._password, safe='@')}"
            )
            resp = await client.post(
                f"{self._base}/api/auth",
                content=raw_body.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5,
            )
            if resp.status_code == 200:
                utoken = resp.json().get("utoken")
                if utoken:
                    logger.debug("LTU login OK (%s) — utoken obtained", self._base)
                    return utoken
                logger.warning("LTU login OK but no utoken in response (%s)", self._base)
            else:
                logger.warning("LTU login failed (%s): HTTP %d", self._base, resp.status_code)
        except httpx.RequestError as exc:
            logger.debug("LTU login unreachable (%s): %s", self._base, exc)
        return None

    async def fetch_stats(self) -> dict | None:
        """
        Authenticate, then GET /api/v1.0/statistics with x-auth-token header.
        Returns the raw JSON dict or None.
        """
        try:
            async with httpx.AsyncClient(timeout=8, verify=get_settings().tls_verify_devices) as client:
                utoken = await self._login(client)
                if utoken is None:
                    return None
                headers = {"x-auth-token": utoken}
                resp = await client.get(
                    f"{self._base}/api/v1.0/statistics",
                    headers=headers,
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Response may be a list wrapping a single object
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    logger.debug("LTU API (%s) statistics → keys=%s", self._base, list(data.keys()) if isinstance(data, dict) else type(data))
                    return data
                logger.warning("LTU API stats failed (%s): HTTP %d", self._base, resp.status_code)
        except httpx.RequestError as exc:
            logger.debug("LTU API unreachable (%s): %s", self._base, exc)
        except Exception as exc:
            logger.error("LTU API unexpected error (%s): %s", self._base, exc)
        return None


def _nested(obj: dict, *keys: str) -> object:
    """Walk a nested dict by key path; return None if any key is missing."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
        if obj is None:
            return None
    return obj


def _float(val: object) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_peer_radio_metrics(peer: dict) -> dict[str, float | None]:
    """Extract per-peer radio metrics from a single ``wireless.peers[i]`` entry.

    The AP-side ``noise_dbm`` (read from ``radios[0].noiseFloor``) is shared
    across peers and is intentionally NOT part of this dict — callers that
    need it add it on top.
    """
    result: dict[str, float | None] = {
        # AP-side metrics (local) — measured at AP for this peer's uplink
        "signal_dbm":        None,
        "ccq_pct":           None,
        "ul_ccq_pct":        None,
        "cinr_db":           None,
        "ul_cinr_db":        None,
        "tx_rate_mbps":      None,
        "rx_rate_mbps":      None,
        "tx_ideal_mbps":     None,
        "rx_ideal_mbps":     None,
        # CPE-side metrics (remote)
        "remote_signal_dbm": None,
        "remote_noise_dbm":  None,
        "remote_eirp_dbm":   None,
        # Peer system info
        "distance_m":        None,
        "peer_uptime_s":     None,
        "peer_cpu_pct":      None,
        "peer_ram_pct":      None,
        "peer_tx_kbps":      None,
        "peer_rx_kbps":      None,
    }

    if not isinstance(peer, dict):
        return result

    # Common peer info (distance, uptime, CPU, RAM, throughput counters)
    common = peer.get("common")
    if isinstance(common, dict):
        result["distance_m"]   = _float(common.get("distance"))
        result["peer_uptime_s"] = _float(common.get("uptime"))
        result["peer_cpu_pct"] = _float(common.get("cpu"))
        result["peer_ram_pct"] = _float(common.get("ram"))
        counters = common.get("counters")
        if isinstance(counters, dict):
            # Try Kbps keys first, fall back to generic tx/rx (use is not None to keep 0 values)
            tx = counters.get("txkbps")
            result["peer_tx_kbps"] = _float(tx if tx is not None else counters.get("tx"))
            rx = counters.get("rxkbps")
            result["peer_rx_kbps"] = _float(rx if rx is not None else counters.get("rx"))

    # Local side — metrics measured at the AP (uplink: CPE → AP)
    local = peer.get("local")
    if isinstance(local, list) and local:
        lq = local[0].get("linkQuality", {})
        result["signal_dbm"]    = _float(lq.get("signal"))
        result["cinr_db"]       = _float(_nested(lq, "cinr", "dl"))
        result["ul_cinr_db"]    = _float(_nested(lq, "cinr", "ul"))
        result["ccq_pct"]       = _float(_nested(lq, "linkScore", "dl"))
        result["ul_ccq_pct"]    = _float(_nested(lq, "linkScore", "ul"))
        # Actual capacity (Kbps → Mbps)
        dl_kbps = _float(_nested(lq, "capacity", "dl"))
        ul_kbps = _float(_nested(lq, "capacity", "ul"))
        if dl_kbps is not None:
            result["tx_rate_mbps"] = dl_kbps / 1000.0
        if ul_kbps is not None:
            result["rx_rate_mbps"] = ul_kbps / 1000.0
        # Ideal (uncapped) capacity
        dl_ideal = _float(_nested(lq, "capacity", "dlIdeal"))
        ul_ideal = _float(_nested(lq, "capacity", "ulIdeal"))
        if dl_ideal is not None:
            result["tx_ideal_mbps"] = dl_ideal / 1000.0
        if ul_ideal is not None:
            result["rx_ideal_mbps"] = ul_ideal / 1000.0

    # Remote side — metrics measured at the CPE (downlink: AP → CPE)
    remote = peer.get("remote")
    if isinstance(remote, list) and remote:
        rlq = remote[0].get("linkQuality", {})
        result["remote_signal_dbm"] = _float(rlq.get("signal"))
        noise = rlq.get("noiseFloor")
        result["remote_noise_dbm"]  = _float(noise if noise is not None else rlq.get("noise"))
        eirp = rlq.get("outputPower")
        result["remote_eirp_dbm"]   = _float(eirp if eirp is not None else rlq.get("eirp"))

    return result


def parse_rocket_ap_metrics(raw: dict) -> dict[str, float | None]:
    """Extract AP-wide metrics that describe the Rocket itself.

    Per-link metrics (signal, CCQ, CINR, rates, distance…) belong to each
    connected LR, NOT to the Rocket — they are extracted separately by
    `parse_per_peer_metrics` and stored against the LR's device_id.

    Currently AP-wide metrics from the LTU HTTP API are:
      noise_dbm  : noise floor at the AP radio (wireless.radios[0].noiseFloor)

    Additional Rocket-level metrics (radio_if_up, eth_if_up, byte counters)
    come from a separate SNMP IF-MIB poll, not this function.
    """
    wireless = raw.get("wireless") if isinstance(raw, dict) else None
    radios = wireless.get("radios") if isinstance(wireless, dict) else None
    noise_dbm: float | None = None
    if isinstance(radios, list) and radios:
        noise_dbm = _float(_nested(radios[0], "noiseFloor"))
    return {"noise_dbm": noise_dbm}


def parse_per_peer_metrics(
    raw: dict,
) -> list[tuple[str | None, dict[str, float | None]]]:
    """Return per-peer radio metrics — one entry per connected CPE.

    Each tuple is ``(mac_normalized_or_None, metrics_dict)``. The MAC is the
    stable identity used by ``discovery_service.reconcile_peers`` to bind a
    peer to its child LR Device. The metrics dict has the same shape as
    ``_extract_peer_radio_metrics`` (no ``noise_dbm`` — that is AP-wide).
    """
    wireless = raw.get("wireless") if isinstance(raw, dict) else None
    if not isinstance(wireless, dict):
        return []
    peers = wireless.get("peers")
    if not isinstance(peers, list):
        return []

    out: list[tuple[str | None, dict[str, float | None]]] = []
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        mac: str | None = None
        common = peer.get("common")
        if isinstance(common, dict):
            mac = _parse_peer_common(common).get("mac")
        out.append((mac, _extract_peer_radio_metrics(peer)))
    return out


def _parse_peer_common(common: dict) -> dict[str, str | None]:
    """Extract identification fields from a peer's 'common' dict.

    The MAC address is normalised to lowercase colon notation so equality
    comparisons in the discovery service work regardless of how the device
    formats the field (uppercase, dashes, dots, no separator).
    """
    info: dict[str, str | None] = {
        "mgmt_ip":  common.get("mgmtIp") or None,
        "hostname": common.get("hostname") or None,
        "model":    None,
        "firmware": None,
        "mac":      None,
    }
    ident = common.get("identification")
    if isinstance(ident, dict):
        info["model"]    = ident.get("model") or None
        info["firmware"] = ident.get("firmwareVersion") or None
        raw_mac = ident.get("mac")
        if raw_mac:
            try:
                from app.schemas.device import normalize_mac
                info["mac"] = normalize_mac(raw_mac)
            except ValueError:
                logger.debug("LTU peer reported invalid MAC %r — kept as None", raw_mac)
    return info


def parse_ltu_peer_info(raw: dict) -> dict[str, str | None]:
    """
    Extract non-numeric peer info from /api/v1.0/statistics response.
    Returns mgmt_ip, hostname, model, firmware, mac of the first connected CPE.
    """
    empty: dict[str, str | None] = {
        "mgmt_ip": None, "hostname": None, "model": None, "firmware": None, "mac": None,
    }
    wireless = raw.get("wireless") if isinstance(raw, dict) else None
    if not isinstance(wireless, dict):
        return empty
    peers = wireless.get("peers")
    if not isinstance(peers, list) or not peers:
        return empty
    common = peers[0].get("common")
    if not isinstance(common, dict):
        return empty
    return _parse_peer_common(common)


def parse_all_peers_info(raw: dict) -> list[dict[str, str | None]]:
    """
    Extract identification info for ALL connected CPEs (peers) reported by the Rocket.
    Returns a list of {mgmt_ip, hostname, model, firmware, mac} dicts — one per peer.
    """
    wireless = raw.get("wireless") if isinstance(raw, dict) else None
    if not isinstance(wireless, dict):
        return []
    peers = wireless.get("peers")
    if not isinstance(peers, list):
        return []
    result = []
    for peer in peers:
        common = peer.get("common")
        if isinstance(common, dict):
            result.append(_parse_peer_common(common))
    return result


async def collect_ltu_api_full(
    host: str,
    username: str = "ubnt",
    password: str = "ubnt",
    port: int = 443,
) -> tuple[
    dict[str, float | None] | None,
    list[dict[str, str | None]],
    list[tuple[str | None, dict[str, float | None]]],
]:
    """Single HTTP call returning ``(rocket_ap_metrics, all_peers, per_peer_metrics)``.

    rocket_ap_metrics — AP-wide floats for the Rocket itself (noise_dbm). None
                        if the Rocket is unreachable.
    all_peers         — identification dicts for every connected LR.
    per_peer_metrics  — ``[(mac, metrics_dict), ...]`` one entry per LR peer
                        (signal, CCQ, CINR, rates, distance…). Stored against
                        each LR's device_id, NOT against the Rocket.
    """
    client = LTUApiClient(host, username, password, port)
    raw = await client.fetch_stats()
    if raw is None:
        return None, [], []
    rocket_ap_metrics = parse_rocket_ap_metrics(raw)
    all_peers         = parse_all_peers_info(raw)
    per_peer_metrics  = parse_per_peer_metrics(raw)
    peer_ips = [p.get("mgmt_ip") or "?" for p in all_peers]
    logger.info(
        "LTU API %s — peers=%s rocket_ap=%s",
        host,
        peer_ips,
        " ".join(f"{k}={v}" for k, v in rocket_ap_metrics.items() if v is not None) or "no data",
    )
    return rocket_ap_metrics, all_peers, per_peer_metrics
