import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# Captures the first RTT from `ping` output, e.g. "time=12.3 ms" or "time=0.123 ms"
_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)


async def ping_host(
    ip_address: str, timeout: int = 2, count: int = 2
) -> tuple[bool, float | None]:
    """
    Ping a host via ICMP using the system ping command.

    Sends ``count`` packets and considers the host **reachable if AT LEAST ONE**
    replies (``ping`` exits 0). A single probe (the old ``-c 1``) was far too
    fragile: Ubiquiti radios **rate-limit ICMP to their management CPU**, so a
    device that is forwarding client traffic and answering its HTTPS API can
    still drop the occasional probe — which used to flip its status to "down"
    and exclude it from the API/SNMP polls. Multiple packets tolerate that loss.

    Returns (reachable, latency_ms):
      - reachable: True if at least one packet got a reply
      - latency_ms: RTT of the first reply in ms when reachable, else None
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", str(count), "-W", str(timeout), ip_address,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # ping spaces packets ~1 s apart and waits up to `timeout` for each, so
        # the worst case is ~count*(timeout+1); add margin before we give up.
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=count * (timeout + 1) + 1
        )
        if proc.returncode != 0:
            return False, None

        match = _RTT_RE.search(stdout.decode("utf-8", errors="replace"))
        latency_ms = float(match.group(1)) if match else None
        return True, latency_ms
    except TimeoutError:
        logger.debug("Ping timeout for %s", ip_address)
        return False, None
    except OSError as exc:
        logger.error("Ping error for %s: %s", ip_address, exc)
        return False, None


async def ping_hosts_bulk(
    ip_addresses: list[str], *, timeout_ms: int = 800, retries: int = 2,
) -> dict[str, bool]:
    """Ping MANY hosts in a single ``fping`` process → ``{ip: reachable}``.

    ``fping`` pings every target in parallel from ONE process (one ICMP socket),
    instead of forking one ``ping`` per host. At 600+ devices this collapses the
    sweep from ~600 subprocesses (~30 s, heavy on the event loop) to a single
    process (~2-5 s) and makes the cost **flat** regardless of parc size.

    A host is "alive" if it answers within ``retries`` + 1 attempts — same
    1-drop tolerance as the old ``ping -c 2`` (Ubiquiti radios rate-limit ICMP
    to their mgmt CPU). Only up/down is returned; supervisor-side RTT is unused.

    Falls back to bounded per-host :func:`ping_host` if ``fping`` is missing or
    errors, so liveness detection never breaks.
    """
    if not ip_addresses:
        return {}
    # -a alive only (printed one IP/line on stdout), -q quiet (no per-probe
    # stats on stderr), -r retries, -t per-probe timeout (ms). fping pings all
    # targets concurrently, so wall time ≈ (retries+1)*timeout, host-count
    # independent; we still cap it generously.
    args = [
        "fping", "-a", "-q", "-r", str(retries), "-t", str(timeout_ms),
        *ip_addresses,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        wall = (retries + 1) * (timeout_ms / 1000.0) + 15
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=wall)
    except FileNotFoundError:
        logger.warning("fping introuvable — fallback ping par hôte")
        return await _ping_bulk_fallback(ip_addresses)
    except TimeoutError:
        logger.warning("fping timeout global — fallback ping par hôte")
        return await _ping_bulk_fallback(ip_addresses)
    except OSError as exc:
        logger.error("fping erreur (%s) — fallback ping par hôte", exc)
        return await _ping_bulk_fallback(ip_addresses)

    alive = {
        line.strip()
        for line in stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    }
    return {ip: (ip in alive) for ip in ip_addresses}


async def _ping_bulk_fallback(
    ip_addresses: list[str], *, concurrency: int = 100,
) -> dict[str, bool]:
    """Fallback: bounded per-host :func:`ping_host` when ``fping`` is unusable.

    Keeps the concurrency bounded (semaphore) so we don't regress to the
    600-simultaneous-subprocess storm that fping was meant to avoid.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(ip: str) -> tuple[str, bool]:
        async with sem:
            reachable, _ = await ping_host(ip)
        return ip, reachable

    results = await asyncio.gather(
        *[_one(ip) for ip in ip_addresses], return_exceptions=True,
    )
    out: dict[str, bool] = {}
    for r in results:
        if isinstance(r, BaseException):
            continue
        ip, reachable = r
        out[ip] = reachable
    return out
