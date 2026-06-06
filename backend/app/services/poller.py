import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# Captures the first RTT from `ping` output, e.g. "time=12.3 ms" or "time=0.123 ms"
_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)


async def ping_host(
    ip_address: str, timeout: int = 2, count: int = 3
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
