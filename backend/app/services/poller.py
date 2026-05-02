import asyncio
import logging

logger = logging.getLogger(__name__)


async def ping_host(ip_address: str, timeout: int = 2) -> bool:
    """
    Ping a host via ICMP using the system ping command.
    Returns True if the host responds, False otherwise.
    Works on Linux (Docker container) and bare-metal servers.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(timeout), ip_address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout + 1)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        logger.debug("Ping timeout for %s", ip_address)
        return False
    except OSError as exc:
        logger.error("Ping error for %s: %s", ip_address, exc)
        return False
