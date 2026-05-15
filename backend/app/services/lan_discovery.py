"""
LAN-side neighbour discovery via the LR.

The customer-side modem (TP-Link, Huawei, ZTE...) sits behind the LR's NAT
and is not directly reachable from the supervisor — but the LR sees it on
its LAN-facing bridge. We open one SSH session to the LR, prime the ARP
cache by pinging the LR's default gateway (which IS the modem in 99 % of
deployments), then read /proc/net/arp and /proc/net/route to enumerate
candidates.

Vendor detection
----------------
We deliberately avoid an OUI table — IEEE assignments evolve and a hardcoded
allow-list rots silently. Instead, for each ARP entry we ask the LR to
`wget` the device's HTTP root and look for TP-Link signatures in headers
and body. Any model whose admin UI responds is detected, including ones
released after this code was written, and the title regex usually pulls
out a model string ("Archer C6", "TL-WR841N", ...) for the operator UI.

Worst case is one HTTP probe per ARP entry, ~3 s timeout each, capped at
LAN_PROBE_LIMIT entries to bound the endpoint latency.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from ipaddress import IPv4Address

import paramiko

from app.services.ssh_service import _exec, _open_transport

logger = logging.getLogger(__name__)


# Cap on how many ARP entries we HTTP-probe in one discovery call. Each probe
# can wait up to wget's timeout (3 s); 10 entries ≈ 30 s upper bound. Real
# residential LANs rarely exceed 3-4 active neighbours.
LAN_PROBE_LIMIT = 10


# Strings that, if found in the HTTP response from a candidate, mark it as
# a TP-Link device. Case-insensitive. Matched against a single concatenation
# of headers + body so a hit anywhere is enough.
_TPLINK_SIGNATURES = (
    re.compile(r"tp-?link", re.IGNORECASE),
    re.compile(r"tplinkwifi\.net", re.IGNORECASE),
    re.compile(r"tplinkmodem\.net", re.IGNORECASE),
    # Product line prefixes — covers Archer (Wi-Fi routers), TL-* (legacy
    # consumer line) and the MR-/M7- 4G router range.
    re.compile(r"\bArcher\s+[A-Z0-9]+", re.IGNORECASE),
    re.compile(r"\bTL-[A-Z]{2,3}\d+[A-Z0-9]*", re.IGNORECASE),
    re.compile(r"\bM[R7]\d+[A-Z0-9]*", re.IGNORECASE),
)

_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Pulls a likely TP-Link model identifier out of a string.
_MODEL_HINT = re.compile(
    r"(Archer\s+[A-Z0-9]+|TL-[A-Z]{2,3}\d+[A-Z0-9]*|M[R7]\d+[A-Z0-9]*)",
    re.IGNORECASE,
)


@dataclass
class LanNeighbor:
    """A candidate modem discovered on the LR's LAN side."""

    ip: str
    mac: str
    interface: str
    is_default_gateway: bool
    vendor: str            # "TP-Link" when the HTTP fingerprint matches
    model_guess: str | None = None  # e.g. "Archer C6" — best-effort from <title>


def _hex_le_to_ipv4(hex_le: str) -> str | None:
    """Decode an /proc/net/route IP field: little-endian hex → dotted quad.

    Example: '0100A8C0' → '192.168.0.1'. Returns None for malformed input.
    """
    if len(hex_le) != 8:
        return None
    try:
        b = bytes.fromhex(hex_le)
        return str(IPv4Address(bytes(reversed(b))))
    except ValueError:
        return None


def _parse_default_gateway(route_proc: str) -> str | None:
    """Find the first 0.0.0.0/0 route in /proc/net/route output and return its gateway IP."""
    for line in route_proc.splitlines()[1:]:  # skip header
        cols = line.split()
        if len(cols) < 3:
            continue
        # Iface Destination Gateway Flags ...
        destination, gateway = cols[1], cols[2]
        if destination == "00000000" and gateway != "00000000":
            return _hex_le_to_ipv4(gateway)
    return None


# /proc/net/arp columns: IP HWtype Flags HWaddress Mask Device
_ARP_LINE = re.compile(
    r"^(?P<ip>\d+\.\d+\.\d+\.\d+)\s+\S+\s+\S+\s+(?P<mac>[0-9a-fA-F:]{17})\s+\S+\s+(?P<dev>\S+)\s*$",
)


def _parse_arp(arp_proc: str) -> list[tuple[str, str, str]]:
    """Return [(ip, mac, interface)] for every valid line in /proc/net/arp.

    Skips header and entries with the all-zero MAC (incomplete ARP lookups).
    """
    out: list[tuple[str, str, str]] = []
    for line in arp_proc.splitlines()[1:]:
        m = _ARP_LINE.match(line)
        if not m:
            continue
        mac = m.group("mac").strip().lower()
        if mac == "00:00:00:00:00:00":
            continue
        out.append((m.group("ip"), mac, m.group("dev")))
    return out


def _exec_capture(transport: paramiko.Transport, command: str, timeout: int = 8) -> str:
    """Run a command and return stdout — best effort, returns '' on error."""
    channel = transport.open_session()
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        chunks: list[bytes] = []
        while True:
            data = channel.recv(4096)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("LAN discovery exec failed: %s — %s", command, exc)
        return ""
    finally:
        channel.close()


def _fingerprint_http(transport: paramiko.Transport, ip: str) -> tuple[bool, str | None]:
    """Probe http://<ip>/ from the LR. Returns (is_tplink, model_guess).

    busybox `wget` is used (curl is rarely present on airOS dropbear).
    `-S` prints headers to stderr, `2>&1` folds them into stdout so the
    Server: header is also scanned. -O - dumps the body to stdout.

    Many TP-Link models 302 to /webpages/login.html or similar — busybox
    wget does NOT follow redirects, but the redirect *response* itself
    usually mentions the product (Server: header, redirect HTML body), so
    a single GET is enough in practice.
    """
    cmd = f"wget -q -S -T 3 -t 1 -O - http://{ip}/ 2>&1"
    out = _exec_capture(transport, cmd, timeout=6)
    if not out:
        return False, None

    is_tplink = any(p.search(out) for p in _TPLINK_SIGNATURES)
    if not is_tplink:
        return False, None

    model: str | None = None
    title = _HTML_TITLE.search(out)
    if title:
        m = _MODEL_HINT.search(title.group(1))
        if m:
            model = m.group(1).strip()
    if model is None:
        # Body scan capped to 8 KB so a giant response doesn't drag regex perf.
        m = _MODEL_HINT.search(out[:8000])
        if m:
            model = m.group(1).strip()
    return True, model


def _discover_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
) -> list[LanNeighbor]:
    """Open SSH to LR, prime ARP, fingerprint each neighbour over HTTP."""
    transport, _observed = _open_transport(
        host=host, port=port, username=username, password=password,
        expected_fingerprint=expected_fingerprint,
    )
    try:
        # 1) Identify the default gateway — most likely the modem we're after.
        route_proc = _exec_capture(transport, "cat /proc/net/route")
        gateway_ip = _parse_default_gateway(route_proc)

        # 2) Prime the ARP cache. dropbear's busybox ping accepts -c / -W.
        if gateway_ip:
            _exec(transport, f"ping -c 2 -W 1 {gateway_ip} >/dev/null 2>&1", timeout=8)

        # 3) Read /proc/net/arp — the canonical busybox view of the cache.
        arp_proc = _exec_capture(transport, "cat /proc/net/arp")
        entries = _parse_arp(arp_proc)

        # 4) Fingerprint each neighbour over HTTP. Probe the gateway first so
        #    the most likely match returns even if we hit LAN_PROBE_LIMIT.
        entries.sort(key=lambda e: (e[0] != gateway_ip, e[0]))
        entries = entries[:LAN_PROBE_LIMIT]

        neighbours: list[LanNeighbor] = []
        for ip, mac, dev in entries:
            is_tplink, model = _fingerprint_http(transport, ip)
            if not is_tplink:
                continue
            neighbours.append(
                LanNeighbor(
                    ip=ip,
                    mac=mac,
                    interface=dev,
                    is_default_gateway=(ip == gateway_ip),
                    vendor="TP-Link",
                    model_guess=model,
                ),
            )
    finally:
        transport.close()

    neighbours.sort(key=lambda n: (not n.is_default_gateway, n.ip))
    logger.info(
        "LAN discovery via %s: %d TP-Link candidate(s) (gateway=%s, probed=%d)",
        host, len(neighbours), gateway_ip, len(entries),
    )
    return neighbours


async def discover_via_lr(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
) -> list[LanNeighbor]:
    """Async wrapper — paramiko is sync, runs in a worker thread."""
    return await asyncio.to_thread(
        _discover_sync, host, port, username, password, expected_fingerprint,
    )
