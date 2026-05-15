"""
SSH diagnostic helpers — synchronous paramiko wrapped in asyncio.to_thread().

Host-key handling
-----------------
We do NOT trust paramiko's `AutoAddPolicy` (which silently accepts any host
key, leaving the supervisor open to MITM on the LAN segment). The fingerprint
check lives inline in `_open_transport`:

  - The fingerprint of the key the device just presented is always recorded
    and returned as the third tuple element.
  - When the caller pinned `expected_fingerprint`, a mismatch raises
    `_FingerprintMismatchError` and aborts the connect.
  - When the caller passes `expected_fingerprint=None` (TOFU — Trust On First
    Use), the key is accepted once and the observed value is returned so the
    caller can persist it on the Device row for next time.

Why we bypass SSHClient
-----------------------
`SSHClient.connect()` always uses `auth_password(fallback=True)`, which probes
`auth_none` before sending the password. Several airOS dropbear builds reject
the subsequent password attempt after that probe — OpenSSH works because it
never sends `auth_none`. We open a `Transport` ourselves and call
`auth_password(fallback=False)` directly.
"""

import asyncio
import base64
import hashlib
import logging
import shlex
import socket

import paramiko

logger = logging.getLogger(__name__)


def _fingerprint(key: paramiko.PKey) -> str:
    """Return the OpenSSH-style SHA256 fingerprint of a paramiko PKey."""
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


class _FingerprintMismatchError(paramiko.SSHException):
    """Raised when the host key fingerprint does not match the pinned one."""


def _open_transport(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
    timeout: int = 6,
) -> tuple[paramiko.Transport, str]:
    """Open an authenticated SSH Transport and return it with the observed fp.

    We bypass `SSHClient.connect()` because it always calls `auth_password` with
    `fallback=True`, which makes paramiko probe an `auth_none` first. Several
    airOS dropbear builds reject the subsequent password attempt after that
    probe — OpenSSH works because it never sends `auth_none`. Calling
    `auth_password(fallback=False)` directly skips the probe and authenticates
    on the first try.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        server_key = transport.get_remote_server_key()
        observed = _fingerprint(server_key)
        if expected_fingerprint is None:
            logger.warning(
                "SSH TOFU: accepting first-seen host key %s for %s — "
                "verify out-of-band and persist this value on the Device row",
                observed, host,
            )
        elif observed != expected_fingerprint:
            raise _FingerprintMismatchError(
                f"Host key mismatch for {host}: "
                f"expected {expected_fingerprint}, got {observed}",
            )
        transport.auth_password(username, password, fallback=False)
    except Exception:
        transport.close()
        raise
    return transport, observed


def _exec(transport: paramiko.Transport, command: str, timeout: int) -> int:
    """Run a command on a new channel and return its exit code."""
    channel = transport.open_session()
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        return channel.recv_exit_status()
    finally:
        channel.close()


def _ssh_check_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    try:
        transport, observed = _open_transport(host, port, username, password, expected_fingerprint)
        transport.close()
        logger.debug("SSH check OK — %s:%d (fp=%s)", host, port, observed)
        return True, "OK", observed
    except _FingerprintMismatchError as exc:
        logger.error("SSH host-key mismatch — %s:%d — %s", host, port, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("SSH check failed — %s:%d — %s", host, port, exc)
        return False, str(exc), None


def _ping_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    try:
        transport, observed = _open_transport(host, port, username, password, expected_fingerprint)
    except _FingerprintMismatchError as exc:
        logger.error("Ping-via-SSH host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("Ping-via-SSH failed — %s — %s", host, exc)
        return False, str(exc), None

    try:
        exit_code = _exec(transport, "ping -c 2 -W 2 8.8.8.8", timeout=10)
        ok = exit_code == 0
        logger.debug("Ping-via-SSH %s → %s (exit %d)", host, "OK" if ok else "KO", exit_code)
        return ok, "Joignable" if ok else "Non joignable", observed
    except Exception as exc:
        logger.debug("Ping-via-SSH exec failed — %s — %s", host, exc)
        return False, str(exc), observed
    finally:
        transport.close()


def _ping_targets_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    targets: list[str],
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    """
    Open one SSH session and try to ping each target IP in order.
    Returns (True, target, observed_fingerprint) as soon as one target
    is reachable, or (False, message, observed_fp) otherwise.
    """
    try:
        transport, observed = _open_transport(host, port, username, password, expected_fingerprint)
    except _FingerprintMismatchError as exc:
        logger.error("ping_targets_via_ssh host-key mismatch %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("ping_targets_via_ssh: SSH connect failed %s — %s", host, exc)
        return False, str(exc), None

    try:
        for target in targets:
            exit_code = _exec(transport, f"ping -c 2 -W 3 {shlex.quote(target)}", timeout=12)
            logger.debug("ping_targets_via_ssh %s → %s exit=%d", host, target, exit_code)
            if exit_code == 0:
                return True, target, observed
        return False, f"Aucune cible joignable parmi {targets}", observed
    finally:
        transport.close()


async def check_ssh_access(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
) -> tuple[bool, str, str | None]:
    """Return (ok, message, observed_fingerprint) — non-blocking."""
    return await asyncio.to_thread(
        _ssh_check_sync, host, port, username, password, expected_fingerprint,
    )


async def check_ping_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
) -> tuple[bool, str, str | None]:
    """SSH into device, run ping 8.8.8.8, return (reachable, message, fp)."""
    return await asyncio.to_thread(
        _ping_via_ssh_sync, host, port, username, password, expected_fingerprint,
    )


async def ping_targets_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    targets: list[str],
    expected_fingerprint: str | None = None,
) -> tuple[bool, str, str | None]:
    """SSH into device and try each target IP in order."""
    return await asyncio.to_thread(
        _ping_targets_via_ssh_sync, host, port, username, password, targets, expected_fingerprint,
    )
