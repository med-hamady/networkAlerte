import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# Captures the RTT from `ping -c 1` output, e.g. "time=12.3 ms" or "time=0.123 ms"
_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)


async def ping_host(ip_address: str, timeout: int = 2) -> tuple[bool, float | None]:
    """
    Ping a host via ICMP using the system ping command.

    Returns (reachable, latency_ms):
      - reachable: True if the host responded
      - latency_ms: round-trip time in ms when reachable, else None
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(timeout), ip_address,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 1
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
