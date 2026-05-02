"""
SSH diagnostic helpers — synchronous paramiko wrapped in asyncio.to_thread().

Host-key handling
-----------------
We do NOT trust paramiko's `AutoAddPolicy` (which silently accepts any host
key, leaving the supervisor open to MITM on the LAN segment). Instead a
custom `_FingerprintPolicy`:

  - Always records the fingerprint of the key the device just presented.
  - Rejects connects whose key fingerprint differs from `expected_fingerprint`
    when the caller pinned one.
  - When the caller passed `expected_fingerprint=None` (TOFU — Trust On First
    Use), accepts the key once and exposes it on `policy.observed_fingerprint`
    so the caller can persist it on the Device row for next time.

Each entry point returns the observed fingerprint as the third tuple element,
so the caller can update Device.ssh_host_fingerprint after a successful first
connect or detect a key change.
"""

import asyncio
import base64
import hashlib
import logging
import shlex

import paramiko

logger = logging.getLogger(__name__)


def _fingerprint(key: paramiko.PKey) -> str:
    """Return the OpenSSH-style SHA256 fingerprint of a paramiko PKey."""
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


class _FingerprintMismatchError(paramiko.SSHException):
    """Raised when the host key fingerprint does not match the pinned one."""


class _FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    """Custom missing-host-key policy that enforces fingerprint pinning.

    See module docstring for semantics.
    """

    def __init__(self, expected_fingerprint: str | None) -> None:
        self.expected_fingerprint = expected_fingerprint
        self.observed_fingerprint: str | None = None

    def missing_host_key(self, client, hostname, key) -> None:  # noqa: ARG002
        observed = _fingerprint(key)
        self.observed_fingerprint = observed
        if self.expected_fingerprint is None:
            logger.warning(
                "SSH TOFU: accepting first-seen host key %s for %s — "
                "verify out-of-band and persist this value on the Device row",
                observed, hostname,
            )
            return
        if observed != self.expected_fingerprint:
            raise _FingerprintMismatchError(
                f"Host key mismatch for {hostname}: "
                f"expected {self.expected_fingerprint}, got {observed}",
            )


def _open_client(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
    timeout: int = 6,
) -> tuple[paramiko.SSHClient, _FingerprintPolicy]:
    client = paramiko.SSHClient()
    policy = _FingerprintPolicy(expected_fingerprint)
    client.set_missing_host_key_policy(policy)
    client.connect(
        host,
        port=port,
        username=username,
        password=password,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client, policy


def _ssh_check_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    try:
        client, policy = _open_client(host, port, username, password, expected_fingerprint)
        client.close()
        logger.debug("SSH check OK — %s:%d (fp=%s)", host, port, policy.observed_fingerprint)
        return True, "OK", policy.observed_fingerprint
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
        client, policy = _open_client(host, port, username, password, expected_fingerprint)
    except _FingerprintMismatchError as exc:
        logger.error("Ping-via-SSH host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("Ping-via-SSH failed — %s — %s", host, exc)
        return False, str(exc), None

    try:
        _, stdout, _ = client.exec_command("ping -c 2 -W 2 8.8.8.8", timeout=10)
        exit_code = stdout.channel.recv_exit_status()
        ok = exit_code == 0
        logger.debug("Ping-via-SSH %s → %s (exit %d)", host, "OK" if ok else "KO", exit_code)
        return ok, "Joignable" if ok else "Non joignable", policy.observed_fingerprint
    except Exception as exc:
        logger.debug("Ping-via-SSH exec failed — %s — %s", host, exc)
        return False, str(exc), policy.observed_fingerprint
    finally:
        client.close()


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
        client, policy = _open_client(host, port, username, password, expected_fingerprint)
    except _FingerprintMismatchError as exc:
        logger.error("ping_targets_via_ssh host-key mismatch %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("ping_targets_via_ssh: SSH connect failed %s — %s", host, exc)
        return False, str(exc), None

    try:
        for target in targets:
            _, stdout, _ = client.exec_command(f"ping -c 2 -W 3 {shlex.quote(target)}", timeout=12)
            exit_code = stdout.channel.recv_exit_status()
            logger.debug("ping_targets_via_ssh %s → %s exit=%d", host, target, exit_code)
            if exit_code == 0:
                return True, target, policy.observed_fingerprint
        return False, f"Aucune cible joignable parmi {targets}", policy.observed_fingerprint
    finally:
        client.close()


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
