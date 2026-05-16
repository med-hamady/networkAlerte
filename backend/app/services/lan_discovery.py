"""
LAN-side neighbour discovery via the LR.

The customer-side modem (TP-Link, Huawei, ZTE...) sits on the LR's LAN —
*downstream* of the LR, behind its NAT — so it is NOT the LR's default
gateway (that points upstream, toward the Rocket / internet). It is also not
directly reachable from the supervisor. We open one SSH session to the LR,
read its routing table, and **ping-sweep only the customer subnet** — the
connected subnet that does NOT contain the default gateway — so every live
host there populates the ARP cache. We then read /proc/net/arp and return
those customer-side neighbours; the operator picks the modem.

Why exclude the gateway's subnet
--------------------------------
The default gateway lives on the WISP management/backbone subnet (e.g.
10.135.0.1 on br0), shared by *every* LR — full of other radios/switches,
never a customer modem, and often a huge /16. The modem is always on a
separate downstream subnet behind the LR's NAT (e.g. 172.16.0.0/x on br1).
Sweeping/returning only the non-gateway subnet keeps the list to the real
modem(s) and avoids walking the backbone. Degenerate single-LAN
deployments (modem on the gateway's subnet) fall back to all subnets.

Why a sweep, not a single gateway ping
--------------------------------------
A previous version pinged only the default gateway and kept only ARP entries
that HTTP-fingerprinted as TP-Link. Both assumptions were wrong: the modem is
downstream (never the gateway) and may not expose a TP-Link banner on :80
(HTTPS-only, generic login page, odd port). The result was a permanently
empty list. The sweep + return-all-customer-neighbours approach fixes that.

Vendor detection (best-effort label only)
-----------------------------------------
For up to FP_LIMIT neighbours we still `wget` the HTTP root and look for
TP-Link signatures, purely to *label* a row ("Archer C6"). A miss never
excludes a candidate — it just leaves the vendor blank.

Subnet sweep is bounded: at most MAX_SWEEP_HOSTS addresses, pinged in
parallel batches of BATCH_SIZE, so a tiny CPE CPU is never flooded and the
endpoint latency stays predictable.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv4Address, IPv4Network

import paramiko

from app.services.ssh_service import _exec, _open_transport

logger = logging.getLogger(__name__)


# Hard cap on swept addresses. A /24 customer LAN (254 hosts) is by far the
# common case; on a larger subnet we sweep only the first MAX_SWEEP_HOSTS
# usable addresses (DHCP pools start low, so the modem is almost always here).
MAX_SWEEP_HOSTS = 256

# Concurrent pings per SSH command. busybox ash backgrounds each ping and
# `wait`s for the batch — keeps the CPE from spawning hundreds of procs at once.
BATCH_SIZE = 48

# Upper bound on HTTP fingerprint probes (vendor label only, ~3 s timeout each).
FP_LIMIT = 12


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
    """A candidate device discovered on the LR's LAN side."""

    ip: str
    mac: str
    interface: str
    is_default_gateway: bool
    vendor: str            # "TP-Link" when the HTTP fingerprint matches, else ""
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


def _parse_connected_subnets(route_proc: str) -> list[tuple[str, IPv4Network]]:
    """Return [(iface, network)] for every directly-connected IPv4 route.

    /proc/net/route columns: Iface Destination Gateway Flags RefCnt Use
    Metric Mask MTU Window IRTT. A connected (on-link) subnet has
    Gateway == 00000000, a non-zero Destination and a non-zero Mask.
    Destination and Mask are little-endian hex. The default route
    (Destination 00000000) is skipped — that is the upstream/WAN side.
    """
    out: list[tuple[str, IPv4Network]] = []
    seen: set[str] = set()
    for line in route_proc.splitlines()[1:]:  # skip header
        cols = line.split()
        if len(cols) < 8:
            continue
        iface, destination, gateway, mask_hex = cols[0], cols[1], cols[2], cols[7]
        if gateway != "00000000":
            continue                       # has a next-hop → not on-link
        if destination == "00000000" or mask_hex == "00000000":
            continue                       # default route or host route — skip
        net_ip = _hex_le_to_ipv4(destination)
        mask_ip = _hex_le_to_ipv4(mask_hex)
        if not net_ip or not mask_ip:
            continue
        try:
            network = IPv4Network(f"{net_ip}/{mask_ip}", strict=False)
        except (AddressValueError, ValueError):
            continue
        key = str(network)
        if key in seen:
            continue
        seen.add(key)
        out.append((iface, network))
    return out


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


def _ip_in_any(ip: str, nets: list[IPv4Network]) -> bool:
    """True if `ip` parses and belongs to any network in `nets`."""
    try:
        addr = IPv4Address(ip)
    except AddressValueError:
        return False
    return any(addr in net for net in nets)


def _split_subnets(
    subnets: list[tuple[str, IPv4Network]],
    gateway_ip: str | None,
) -> tuple[list[tuple[str, IPv4Network]], list[tuple[str, IPv4Network]]]:
    """Partition connected subnets into (customer, uplink).

    The default gateway lives on the WISP management / uplink subnet (e.g.
    10.135.0.1 on br0). The customer modem sits on a *different* connected
    subnet, downstream behind the LR's NAT (e.g. 172.16.0.0/x on br1). So
    "uplink" = every subnet containing the gateway, "customer" = the rest.

    Degenerate fallback: if that leaves no customer subnet (single-LAN
    deployment where the modem shares the gateway's subnet), treat *all*
    connected subnets as customer so discovery still returns something.
    """
    if gateway_ip is None:
        return subnets, []
    try:
        gw = IPv4Address(gateway_ip)
    except AddressValueError:
        return subnets, []
    customer = [(i, n) for i, n in subnets if gw not in n]
    uplink = [(i, n) for i, n in subnets if gw in n]
    if not customer:                       # modem shares the gateway subnet
        return subnets, []
    return customer, uplink


def _sweep_hosts(subnets: list[tuple[str, IPv4Network]]) -> list[str]:
    """Flatten connected subnets into a bounded, de-duplicated host list.

    Hosts are taken subnet by subnet (lowest addresses first, where DHCP
    pools live) until MAX_SWEEP_HOSTS is reached.
    """
    hosts: list[str] = []
    for _iface, network in subnets:
        if network.prefixlen >= 31:        # /31, /32 — no usable host range
            continue
        for addr in network.hosts():
            hosts.append(str(addr))
            if len(hosts) >= MAX_SWEEP_HOSTS:
                return hosts
    return hosts


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


def _ping_sweep(transport: paramiko.Transport, hosts: list[str]) -> None:
    """Populate the LR's ARP cache by pinging every host, in parallel batches.

    busybox ping on airOS dropbear accepts -c (count) and -W (timeout, s).
    Each batch backgrounds its pings and `wait`s, so concurrency is capped at
    BATCH_SIZE regardless of subnet size. Failures are ignored — a silent
    host simply won't get an ARP entry, which is the expected outcome.
    """
    for i in range(0, len(hosts), BATCH_SIZE):
        batch = hosts[i : i + BATCH_SIZE]
        joined = " ".join(batch)
        cmd = (
            f'for ip in {joined}; do '
            f'ping -c 1 -W 1 "$ip" >/dev/null 2>&1 & done; wait'
        )
        # Worst case: one straggler ≈ 1 s after the rest; give the batch ample
        # headroom so `wait` never gets cut off mid-sweep.
        _exec(transport, cmd, timeout=BATCH_SIZE + 10)


def _fingerprint_http(transport: paramiko.Transport, ip: str) -> tuple[bool, str | None]:
    """Probe http://<ip>/ from the LR. Returns (is_tplink, model_guess).

    busybox `wget` is used (curl is rarely present on airOS dropbear).
    `-S` prints headers to stderr, `2>&1` folds them into stdout so the
    Server: header is also scanned. -O - dumps the body to stdout.

    Many TP-Link models 302 to /webpages/login.html or similar — busybox
    wget does NOT follow redirects, but the redirect *response* itself
    usually mentions the product (Server: header, redirect HTML body), so
    a single GET is enough in practice. A non-match is not fatal: the
    neighbour is still returned, just without a vendor label.
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
    """Open SSH to LR, sweep its customer LAN subnet, return its neighbours.

    Only the connected subnet(s) that do NOT contain the default gateway
    are swept and returned — that is the customer side where the modem
    lives. The WISP management/backbone subnet (the one with the gateway,
    e.g. 10.135/16 shared by every LR) is skipped entirely so the list is
    just the real modem(s), not infra.
    """
    transport, _observed = _open_transport(
        host=host, port=port, username=username, password=password,
        expected_fingerprint=expected_fingerprint,
    )
    try:
        # 1) Routing table: default gateway + directly-connected subnets,
        #    split into customer (no gateway) vs uplink/backbone (gateway).
        route_proc = _exec_capture(transport, "cat /proc/net/route")
        gateway_ip = _parse_default_gateway(route_proc)
        subnets = _parse_connected_subnets(route_proc)
        customer_subnets, uplink_subnets = _split_subnets(subnets, gateway_ip)
        uplink_nets = [n for _i, n in uplink_subnets]
        customer_nets = [n for _i, n in customer_subnets]

        # 2) Ping-sweep ONLY the customer subnet(s) so the modem (often
        #    silent) lands in the ARP cache — and we never waste the host
        #    budget walking a huge backbone /16.
        sweep = _sweep_hosts(customer_subnets)
        if sweep:
            _ping_sweep(transport, sweep)

        # 3) Read /proc/net/arp — the canonical busybox view of the cache.
        arp_proc = _exec_capture(transport, "cat /proc/net/arp")
        entries = _parse_arp(arp_proc)

        # 4) Keep only customer-side neighbours. An entry qualifies if it
        #    sits in a customer subnet, OR (defensive, for firmware whose
        #    /proc/net/route omits the LAN route) it is neither inside the
        #    uplink/backbone subnet nor the gateway itself.
        kept: list[tuple[str, str, str]] = []
        for ip, mac, dev in entries:
            if ip == gateway_ip:
                continue
            in_customer = _ip_in_any(ip, customer_nets)
            in_uplink = _ip_in_any(ip, uplink_nets)
            if in_customer or (not in_uplink and customer_nets):
                kept.append((ip, mac, dev))
        kept.sort(key=lambda e: _ip_sort_key(e[0]))

        # 5) Best-effort vendor label on the first FP_LIMIT entries. A miss
        #    never drops the row — every customer neighbour is returned.
        neighbours: list[LanNeighbor] = []
        probed = 0
        for ip, mac, dev in kept:
            vendor, model = "", None
            if probed < FP_LIMIT:
                probed += 1
                is_tplink, model_guess = _fingerprint_http(transport, ip)
                if is_tplink:
                    vendor, model = "TP-Link", model_guess
            neighbours.append(
                LanNeighbor(
                    ip=ip,
                    mac=mac,
                    interface=dev,
                    is_default_gateway=False,  # gateway is excluded above
                    vendor=vendor,
                    model_guess=model,
                ),
            )
    finally:
        transport.close()

    # TP-Link-labelled first, then by IP.
    neighbours.sort(key=lambda n: (n.vendor != "TP-Link", _ip_sort_key(n.ip)))
    logger.info(
        "LAN discovery via %s: %d modem candidate(s) "
        "(gateway=%s, customer=%s, uplink=%s, swept=%d)",
        host,
        len(neighbours),
        gateway_ip,
        [str(n) for _i, n in customer_subnets] or "none",
        [str(n) for _i, n in uplink_subnets] or "none",
        len(sweep),
    )
    return neighbours


def _ip_sort_key(ip: str) -> tuple[int, ...]:
    """Numeric sort key for a dotted IPv4 (string sort would put .10 before .2)."""
    try:
        return tuple(int(p) for p in ip.split("."))
    except ValueError:
        return (0,)


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
