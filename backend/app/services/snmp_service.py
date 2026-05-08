"""
SNMP service for Ubiquiti LTU and airMAX devices and standard equipment.

Supported equipment:
  - LTU Rocket / LTU LR  : IF-MIB (ath0/eth0 status + byte counters)
  - airMAX (airOS)        : UBNT Enterprise MIB (1.3.6.1.4.1.41112.1.4.5) +
                            IF-MIB for interface status and error counters
  - UISP Switch           : standard MIB-II (ports, speeds, errors)

Note: LTU firmware v2.x does NOT expose the Ubiquiti airMAX enterprise MIB.
airOS devices (Rocket M, NanoStation, etc.) expose it at OID prefix
1.3.6.1.4.1.41112.1.4.5 (ubntAirIf station table).

OID reference (IF-MIB):
  - sysUpTime      : 1.3.6.1.2.1.1.3.0        (uptime, timeticks)
  - ifDescr        : 1.3.6.1.2.1.2.2.1.2.{i}  (interface name)
  - ifOperStatus   : 1.3.6.1.2.1.2.2.1.8.{i}  (1=up, 2=down)
  - ifInOctets     : 1.3.6.1.2.1.2.2.1.10.{i} (bytes received)
  - ifOutOctets    : 1.3.6.1.2.1.2.2.1.16.{i} (bytes transmitted)
  - ifInErrors     : 1.3.6.1.2.1.2.2.1.14.{i} (receive errors)
  - ifOutErrors    : 1.3.6.1.2.1.2.2.1.20.{i} (transmit errors)

OID reference (UBNT Enterprise MIB — airOS only):
  - ubntStaTxRate     : 1.3.6.1.4.1.41112.1.4.5.1.2.1  (TX rate, Kbps)
  - ubntStaRxRate     : 1.3.6.1.4.1.41112.1.4.5.1.3.1  (RX rate, Kbps)
  - ubntStaCCQ        : 1.3.6.1.4.1.41112.1.4.5.1.4.1  (CCQ ×10, e.g. 995 = 99.5%)
  - ubntStaRemoteSignal: 1.3.6.1.4.1.41112.1.4.5.1.5.1 (AP-side signal, dBm)
  - ubntStaNoise      : 1.3.6.1.4.1.41112.1.4.5.1.6.1  (noise floor, dBm)
"""

import contextlib
import logging
from typing import Any

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
)

logger = logging.getLogger(__name__)

# Singleton SnmpEngine — pysnmp keeps a UDP socket per engine instance,
# so creating a fresh engine per call leaks file descriptors over time.
# A single shared engine is safe across coroutines (pysnmp serializes
# transport access internally) and survives the whole process lifetime.
_engine: SnmpEngine | None = None


def _get_engine() -> SnmpEngine:
    global _engine
    if _engine is None:
        _engine = SnmpEngine()
    return _engine


def close_snmp_engine() -> None:
    """Close the shared SNMP engine and release its UDP socket.

    Call this once at application shutdown (FastAPI lifespan teardown) to
    avoid file-descriptor leaks when the process restarts cleanly.
    """
    global _engine
    if _engine is not None:
        _engine.transportDispatcher.closeDispatcher()
        _engine = None
        logger.debug("SNMP engine closed")


# Standard OIDs available on all SNMP devices
STANDARD_OIDS: dict[str, str] = {
    "uptime_seconds": "1.3.6.1.2.1.1.3.0",
}

# IF-MIB base OIDs (append .{interface_index})
_IF_DESCR       = "1.3.6.1.2.1.2.2.1.2"
_IF_OPER        = "1.3.6.1.2.1.2.2.1.8"
_IF_SPEED       = "1.3.6.1.2.1.2.2.1.5"    # bits/sec
_IF_IN_OCTETS   = "1.3.6.1.2.1.2.2.1.10"
_IF_OUT_OCTETS  = "1.3.6.1.2.1.2.2.1.16"
_IF_IN_ERRORS   = "1.3.6.1.2.1.2.2.1.14"
_IF_OUT_ERRORS  = "1.3.6.1.2.1.2.2.1.20"
_IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
_IF_OUT_DISCARDS= "1.3.6.1.2.1.2.2.1.19"
# IF-MIB 64-bit counters (ifXTable)
_IF_HC_IN       = "1.3.6.1.2.1.31.1.1.1.6"
_IF_HC_OUT      = "1.3.6.1.2.1.31.1.1.1.10"

# Interface name patterns for radio and Ethernet interfaces on Ubiquiti LTU
_RADIO_IF_NAMES = {"ath0", "ath1", "wlan0", "wlan1"}
_ETH_IF_NAMES   = {"eth0", "eth1", "ether0", "ether1"}

# UBNT Enterprise MIB — airOS station table (ubntAirIf), station index 1
# Each OID ends with .1 for the first (and usually only) wireless peer/link.
_UBNT_STA_TX_RATE = "1.3.6.1.4.1.41112.1.4.5.1.2.1"  # TX rate (Kbps)
_UBNT_STA_RX_RATE = "1.3.6.1.4.1.41112.1.4.5.1.3.1"  # RX rate (Kbps)
_UBNT_STA_CCQ     = "1.3.6.1.4.1.41112.1.4.5.1.4.1"  # CCQ ×10 (e.g. 995 = 99.5%)
_UBNT_STA_SIGNAL  = "1.3.6.1.4.1.41112.1.4.5.1.5.1"  # Remote signal (dBm, negative int)
_UBNT_STA_NOISE   = "1.3.6.1.4.1.41112.1.4.5.1.6.1"  # Noise floor (dBm)

# UBNT Enterprise MIB — airOS multi-peer station table (ubntStaTable)
# Used by Rocket M / NanoStation M when multiple CPE clients are associated.
# Walking these tables exposes every connected peer (one row per peer).
_UBNT_STA_MAC_BASE     = "1.3.6.1.4.1.41112.1.4.7.1.1"   # MAC (OctetString)
_UBNT_STA_LASTIP_BASE  = "1.3.6.1.4.1.41112.1.4.7.1.14"  # Last known IPv4 (varies by firmware)
_UBNT_STA_HOSTNAME_BASE = "1.3.6.1.4.1.41112.1.4.7.1.5"  # Station hostname when published
_UBNT_STA_TABLE_MAX    = 64                               # cap walk to avoid runaway loops


async def _snmp_get(
    engine: SnmpEngine,
    host: str,
    community: str,
    oid: str,
    port: int,
    timeout: int,
    mp_model: int = 1,
) -> Any | None:
    """Perform a single SNMP GET. Returns the raw value or None on any error.

    `mp_model` selects the SNMP version: 0 = SNMPv1, 1 = SNMPv2c (default).
    Older airOS firmwares (Rocket Prism / XC v8.x) only answer in SNMPv1.
    """
    try:
        error_indication, error_status, _, var_binds = await getCmd(
            engine,
            CommunityData(community, mpModel=mp_model),
            UdpTransportTarget((host, port), timeout=timeout, retries=0),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        if error_indication:
            logger.debug("SNMP error (%s, %s): %s", host, oid, error_indication)
            return None
        if error_status:
            logger.debug("SNMP status error (%s, %s): %s", host, oid, error_status)
            return None
        for var_bind in var_binds:
            return var_bind[1]
    except Exception as exc:
        logger.debug("SNMP exception (%s, %s): %s", host, oid, exc)
    return None


async def _find_if_index(
    engine: SnmpEngine,
    host: str,
    community: str,
    port: int,
    timeout: int,
    if_names: set[str],
    mp_model: int = 1,
) -> int | None:
    """Walk ifDescr (up to 20 interfaces) to find an interface matching if_names."""
    for i in range(1, 20):
        raw = await _snmp_get(engine, host, community, f"{_IF_DESCR}.{i}", port, timeout, mp_model)
        if raw is not None:
            name = str(raw).strip().lower()
            if name in if_names:
                logger.debug("Interface %r found at index %d on %s", name, i, host)
                return i
    return None


async def collect_ltu_metrics(
    host: str,
    community: str = "public",
    port: int = 161,
    timeout: int = 2,
) -> dict[str, float | None]:
    """
    Collect radio interface metrics from LTU Rocket / LTU LR via standard IF-MIB.

    LTU firmware v2.x does not expose the Ubiquiti airMAX enterprise MIB.
    Instead we monitor the ath0 wireless interface via standard IF-MIB:
      - radio_if_up    : 1.0 = interface UP, 0.0 = DOWN
      - radio_rx_bytes : cumulative received bytes (Counter32)
      - radio_tx_bytes : cumulative transmitted bytes (Counter32)
      - radio_in_errors / radio_out_errors : error counters
      - uptime_seconds : device uptime
    """
    engine = _get_engine()
    metrics: dict[str, float | None] = {
        "radio_if_up":    None,
        "radio_rx_bytes": None,
        "radio_tx_bytes": None,
        "radio_in_errors":  None,
        "radio_out_errors": None,
        "eth_if_up":      None,
        "uptime_seconds": None,
    }

    # Uptime (standard)
    raw = await _snmp_get(engine, host, community, "1.3.6.1.2.1.1.3.0", port, timeout)
    if raw is not None:
        with contextlib.suppress(TypeError, ValueError):
            metrics["uptime_seconds"] = round(int(raw) / 100.0, 1)

    # Find radio interface index (ath0 / wlan0)
    idx = await _find_if_index(engine, host, community, port, timeout, _RADIO_IF_NAMES)
    if idx is None:
        logger.warning("Radio interface (ath0) not found via SNMP on %s", host)
        return metrics

    # ifOperStatus: 1=up, 2=down
    status = await _snmp_get(engine, host, community, f"{_IF_OPER}.{idx}", port, timeout)
    if status is not None:
        with contextlib.suppress(TypeError, ValueError):
            metrics["radio_if_up"] = 1.0 if int(status) == 1 else 0.0

    # Traffic and error counters
    for metric, base_oid in [
        ("radio_rx_bytes",   _IF_IN_OCTETS),
        ("radio_tx_bytes",   _IF_OUT_OCTETS),
        ("radio_in_errors",  _IF_IN_ERRORS),
        ("radio_out_errors", _IF_OUT_ERRORS),
    ]:
        raw = await _snmp_get(engine, host, community, f"{base_oid}.{idx}", port, timeout)
        if raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                metrics[metric] = float(raw)

    # Find Ethernet interface index (eth0) — wired link to the switch
    eth_idx = await _find_if_index(engine, host, community, port, timeout, _ETH_IF_NAMES)
    if eth_idx is not None:
        eth_status = await _snmp_get(engine, host, community, f"{_IF_OPER}.{eth_idx}", port, timeout)
        if eth_status is not None:
            with contextlib.suppress(TypeError, ValueError):
                metrics["eth_if_up"] = 1.0 if int(eth_status) == 1 else 0.0

    return metrics


async def collect_airmax_metrics(
    host: str,
    community: str = "public",
    port: int = 161,
    timeout: int = 2,
    mp_model: int = 0,
) -> dict[str, float | None]:
    """
    Collect radio metrics from an airOS device (Rocket M, NanoStation, etc.)
    via the UBNT Enterprise MIB (1.3.6.1.4.1.41112.1.4.5) plus standard IF-MIB.

    Returns metrics compatible with alert_rules.py:
      signal_dbm     : AP-side signal strength (dBm, negative)
      noise_dbm      : noise floor (dBm)
      cinr_db        : computed as signal − noise (approximates SNR)
      ccq_pct        : CCQ in percent (0–100)
      tx_rate_mbps   : current TX throughput (Mbps)
      rx_rate_mbps   : current RX throughput (Mbps)
      radio_if_up    : 1.0=UP / 0.0=DOWN (IF-MIB ath0 ifOperStatus)
      eth_if_up      : 1.0=UP / 0.0=DOWN (IF-MIB eth0 ifOperStatus)
      radio_rx_bytes : cumulative RX bytes (IF-MIB, for error-rate tracking)
      radio_tx_bytes : cumulative TX bytes
      radio_in_errors / radio_out_errors : IF-MIB error counters
      uptime_seconds : device uptime
    """
    engine = _get_engine()
    metrics: dict[str, float | None] = {
        "radio_if_up":    None,
        "radio_rx_bytes": None,
        "radio_tx_bytes": None,
        "radio_in_errors":  None,
        "radio_out_errors": None,
        "eth_if_up":      None,
        "uptime_seconds": None,
        "signal_dbm":     None,
        "noise_dbm":      None,
        "cinr_db":        None,
        "ccq_pct":        None,
        "tx_rate_mbps":   None,
        "rx_rate_mbps":   None,
    }

    # Standard uptime
    raw = await _snmp_get(engine, host, community, "1.3.6.1.2.1.1.3.0", port, timeout, mp_model)
    if raw is not None:
        with contextlib.suppress(TypeError, ValueError):
            metrics["uptime_seconds"] = round(int(raw) / 100.0, 1)

    # UBNT Enterprise MIB — wireless station stats
    airmax_poll: list[tuple[str, str, object]] = [
        ("tx_rate_mbps", _UBNT_STA_TX_RATE, lambda v: round(int(v) / 1000.0, 2)),
        ("rx_rate_mbps", _UBNT_STA_RX_RATE, lambda v: round(int(v) / 1000.0, 2)),
        ("ccq_pct",      _UBNT_STA_CCQ,     lambda v: round(int(v) / 10.0, 1)),
        ("signal_dbm",   _UBNT_STA_SIGNAL,  lambda v: float(int(v))),
        ("noise_dbm",    _UBNT_STA_NOISE,   lambda v: float(int(v))),
    ]
    for metric_key, oid, transform in airmax_poll:
        raw = await _snmp_get(engine, host, community, oid, port, timeout, mp_model)
        if raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                metrics[metric_key] = transform(raw)  # type: ignore[operator]

    # Derive CINR from signal − noise when both are available.
    # Quirk: airOS 8 / XC firmware (Rocket Prism 5AC, etc.) exposes the SNR
    # directly in the "noise" OID as a positive value, instead of the noise
    # floor in negative dBm. Detect that case and re-interpret: the value IS
    # the CINR, and the actual noise floor is (signal − CINR).
    if metrics["signal_dbm"] is not None and metrics["noise_dbm"] is not None:
        if metrics["noise_dbm"] >= 0:  # type: ignore[operator]
            metrics["cinr_db"]  = round(metrics["noise_dbm"], 1)  # type: ignore[arg-type]
            metrics["noise_dbm"] = round(
                metrics["signal_dbm"] - metrics["cinr_db"], 1  # type: ignore[operator]
            )
        else:
            metrics["cinr_db"] = round(
                metrics["signal_dbm"] - metrics["noise_dbm"], 1  # type: ignore[operator]
            )

    # IF-MIB — radio interface (ath0) status and byte/error counters
    idx = await _find_if_index(engine, host, community, port, timeout, _RADIO_IF_NAMES, mp_model)
    if idx is None:
        logger.warning("Radio interface (ath0) not found via SNMP on %s", host)
    else:
        status = await _snmp_get(engine, host, community, f"{_IF_OPER}.{idx}", port, timeout, mp_model)
        if status is not None:
            with contextlib.suppress(TypeError, ValueError):
                metrics["radio_if_up"] = 1.0 if int(status) == 1 else 0.0

        for metric, base_oid in [
            ("radio_rx_bytes",   _IF_IN_OCTETS),
            ("radio_tx_bytes",   _IF_OUT_OCTETS),
            ("radio_in_errors",  _IF_IN_ERRORS),
            ("radio_out_errors", _IF_OUT_ERRORS),
        ]:
            raw = await _snmp_get(engine, host, community, f"{base_oid}.{idx}", port, timeout, mp_model)
            if raw is not None:
                with contextlib.suppress(TypeError, ValueError):
                    metrics[metric] = float(raw)

    # IF-MIB — Ethernet interface (eth0) status
    eth_idx = await _find_if_index(engine, host, community, port, timeout, _ETH_IF_NAMES, mp_model)
    if eth_idx is not None:
        eth_status = await _snmp_get(engine, host, community, f"{_IF_OPER}.{eth_idx}", port, timeout, mp_model)
        if eth_status is not None:
            with contextlib.suppress(TypeError, ValueError):
                metrics["eth_if_up"] = 1.0 if int(eth_status) == 1 else 0.0

    return metrics


async def collect_switch_port_metrics(
    host: str,
    community: str = "public",
    port: int = 161,
    timeout: int = 2,
    max_ports: int = 16,
) -> dict[str, float | None]:
    """
    Collect full IF-MIB metrics from a managed switch for interfaces 1..max_ports.

    Per-port metrics saved (prefix port_N_):
      _up          : 1.0=UP / 0.0=DOWN  (ifOperStatus)
      _speed_mbps  : link speed in Mbps  (ifSpeed)
      _rx_bytes    : cumulative RX bytes  (ifHCInOctets or ifInOctets)
      _tx_bytes    : cumulative TX bytes  (ifHCOutOctets or ifOutOctets)
      _in_errors   : RX error count      (ifInErrors)
      _out_errors  : TX error count      (ifOutErrors)
      _in_discards : RX discard count    (ifInDiscards)
      _out_discards: TX discard count    (ifOutDiscards)

    Plus: uptime_seconds (sysUpTime).
    """
    engine = _get_engine()
    metrics: dict[str, float | None] = {}

    # System uptime
    raw = await _snmp_get(engine, host, community, "1.3.6.1.2.1.1.3.0", port, timeout)
    if raw is not None:
        with contextlib.suppress(TypeError, ValueError):
            metrics["uptime_seconds"] = round(int(raw) / 100.0, 1)

    found = 0
    for i in range(1, max_ports + 1):
        # ifOperStatus — must succeed to count this port
        status = await _snmp_get(engine, host, community, f"{_IF_OPER}.{i}", port, timeout)
        if status is None:
            continue
        found += 1
        try:
            metrics[f"port_{i}_up"] = 1.0 if int(status) == 1 else 0.0
        except (TypeError, ValueError):
            metrics[f"port_{i}_up"] = None

        # ifSpeed (bits/sec → Mbps)
        speed_raw = await _snmp_get(engine, host, community, f"{_IF_SPEED}.{i}", port, timeout)
        if speed_raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                metrics[f"port_{i}_speed_mbps"] = float(int(speed_raw) / 1_000_000)

        # 64-bit byte counters (ifHCInOctets / ifHCOutOctets) — fall back to 32-bit
        for metric_key, hc_oid, oid32 in [
            (f"port_{i}_rx_bytes",    f"{_IF_HC_IN}.{i}",  f"{_IF_IN_OCTETS}.{i}"),
            (f"port_{i}_tx_bytes",    f"{_IF_HC_OUT}.{i}", f"{_IF_OUT_OCTETS}.{i}"),
        ]:
            v = await _snmp_get(engine, host, community, hc_oid, port, timeout)
            if v is None:
                v = await _snmp_get(engine, host, community, oid32, port, timeout)
            if v is not None:
                with contextlib.suppress(TypeError, ValueError):
                    metrics[metric_key] = float(v)

        # Error and discard counters
        for metric_key, base_oid in [
            (f"port_{i}_in_errors",    _IF_IN_ERRORS),
            (f"port_{i}_out_errors",   _IF_OUT_ERRORS),
            (f"port_{i}_in_discards",  _IF_IN_DISCARDS),
            (f"port_{i}_out_discards", _IF_OUT_DISCARDS),
        ]:
            v = await _snmp_get(engine, host, community, f"{base_oid}.{i}", port, timeout)
            if v is not None:
                with contextlib.suppress(TypeError, ValueError):
                    metrics[metric_key] = float(v)

    logger.debug("Switch %s — %d ports discovered, %d metrics", host, found, len(metrics))
    return metrics


def _format_mac_from_octets(raw: Any) -> str | None:
    """Convert an SNMP MAC value (6-byte OctetString or hex string) to aa:bb:cc:dd:ee:ff."""
    if raw is None:
        return None
    try:
        # pysnmp returns OctetString — prettyPrint() yields "0xAABBCCDDEEFF" or
        # raw bytes; .asOctets() yields the raw 6-byte sequence.
        if hasattr(raw, "asOctets"):
            octets = bytes(raw.asOctets())
            if len(octets) == 6:
                return ":".join(f"{b:02x}" for b in octets)
        s = str(raw).strip()
        # "0xAABBCCDDEEFF" (12 hex chars) or "AA:BB:CC:DD:EE:FF" or "AA-BB-CC-DD-EE-FF"
        if s.startswith("0x"):
            s = s[2:]
        s = s.replace(":", "").replace("-", "").replace(" ", "")
        if len(s) == 12 and all(c in "0123456789abcdefABCDEF" for c in s):
            return ":".join(s[i:i + 2].lower() for i in range(0, 12, 2))
    except Exception:
        pass
    return None


def _format_ip_from_snmp(raw: Any) -> str | None:
    """Convert an SNMP IPv4 value (IpAddress, OctetString, or dotted string) to a.b.c.d."""
    if raw is None:
        return None
    try:
        if hasattr(raw, "asOctets"):
            octets = bytes(raw.asOctets())
            if len(octets) == 4:
                return ".".join(str(b) for b in octets)
        s = str(raw).strip()
        # Already dotted form?
        parts = s.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return s
    except Exception:
        pass
    return None


async def discover_airmax_peers(
    host: str,
    community: str = "public",
    port: int = 161,
    timeout: int = 2,
    mp_model: int = 0,
) -> list[dict[str, str | None]]:
    """Walk the UBNT station table on an airOS Rocket and return peer descriptors.

    Returns a list of dicts shaped for `discovery_service.reconcile_peers`:
      [{"mac": "aa:bb:...", "mgmt_ip": "10.x.y.z", "hostname": "...",
        "model": None, "firmware": None}, ...]

    Empty list when the device exposes no station entries or the MIB branch is
    not implemented (e.g. firmware that ships only the single-station OIDs at
    `.4.5.*.1`). This is non-fatal — the caller treats "no peers" as "nothing
    to reconcile this cycle".
    """
    engine = _get_engine()
    peers: list[dict[str, str | None]] = []

    for i in range(1, _UBNT_STA_TABLE_MAX + 1):
        mac_raw = await _snmp_get(
            engine, host, community, f"{_UBNT_STA_MAC_BASE}.{i}", port, timeout, mp_model,
        )
        if mac_raw is None:
            # First missing index = end of table (UBNT stations are dense from .1)
            break
        mac = _format_mac_from_octets(mac_raw)
        if mac is None:
            # Unparseable MAC at this index — skip but keep walking, sometimes
            # firmware leaves placeholder rows.
            continue

        ip_raw = await _snmp_get(
            engine, host, community, f"{_UBNT_STA_LASTIP_BASE}.{i}", port, timeout, mp_model,
        )
        mgmt_ip = _format_ip_from_snmp(ip_raw)

        hostname_raw = await _snmp_get(
            engine, host, community, f"{_UBNT_STA_HOSTNAME_BASE}.{i}", port, timeout, mp_model,
        )
        hostname = str(hostname_raw).strip() if hostname_raw is not None else None
        # Some firmwares emit non-printable padding for empty hostname slots —
        # drop anything that is not strictly printable ASCII (DNS-safe charset).
        if hostname and not all(c.isprintable() and c.isascii() for c in hostname):
            hostname = None

        peers.append({
            "mac":      mac,
            "mgmt_ip":  mgmt_ip,
            "hostname": hostname or None,
            "model":    None,    # not available via this MIB branch
            "firmware": None,    # not available via this MIB branch
        })

    if peers:
        logger.info(
            "airMAX SNMP discovery on %s — %d peer(s) trouvé(s)", host, len(peers),
        )
    else:
        logger.debug("airMAX SNMP discovery on %s — table de stations vide", host)
    return peers


async def collect_standard_metrics(
    host: str,
    community: str = "public",
    port: int = 161,
    timeout: int = 2,
) -> dict[str, float | None]:
    """
    Collect basic SNMP metrics (uptime) for non-radio devices (Switch, etc.).
    """
    engine = _get_engine()
    metrics: dict[str, float | None] = {}

    for name, oid in STANDARD_OIDS.items():
        raw = await _snmp_get(engine, host, community, oid, port, timeout)
        if raw is None:
            metrics[name] = None
            continue
        try:
            value = int(raw)
            if name == "uptime_seconds":
                metrics[name] = round(value / 100.0, 1)
            else:
                metrics[name] = float(value)
        except (TypeError, ValueError):
            metrics[name] = None

    return metrics
