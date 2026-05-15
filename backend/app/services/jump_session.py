"""
SSH jump session — open an interactive shell on a customer-side modem
(TP-Link / Huawei / ZTE) that sits behind an LR's NAT and is therefore
unreachable from the supervisor directly.

Topology
--------
    supervisor ── ssh ──▶ LR ── direct-tcpip ──▶ modem (private IP)
                                                  └─ ssh / telnet ─▶ shell

We open a paramiko Transport to the LR (reusing _open_transport from
ssh_service so we keep the auth_none-bypass + fingerprint pinning) then ask
that Transport for a `direct-tcpip` channel pointing at the modem's
management address. Wrapping that channel as a socket lets us run a second
paramiko Transport over it, authenticate against the modem, and call
`invoke_shell()` to obtain an interactive PTY channel.

Telnet is reserved for a future iteration — until we have a real customer
modem to test against, the WebSocket endpoint refuses telnet protocol with
501 Not Implemented.

Sync vs async
-------------
paramiko is fully synchronous. Callers MUST run `open_jump_shell` inside
`asyncio.to_thread` so the event loop is not blocked on socket I/O. The
returned `paramiko.Channel` is also blocking — pump bytes through
`asyncio.to_thread` for both directions in the WebSocket bridge.
"""

import contextlib
import logging
from dataclasses import dataclass

import paramiko

from app.services.ssh_service import _fingerprint, _FingerprintMismatchError, _open_transport

logger = logging.getLogger(__name__)


@dataclass
class JumpCreds:
    """Credentials + pinned fingerprint for one hop of the jump."""

    host: str
    port: int
    username: str
    password: str
    expected_fingerprint: str | None = None


@dataclass
class JumpSession:
    """An open jump → shell session. Close via .close() to release both transports."""

    jump_transport: paramiko.Transport
    target_transport: paramiko.Transport
    channel: paramiko.Channel
    target_observed_fingerprint: str

    def close(self) -> None:
        """Close inner-most resources first, swallow errors — best effort."""
        for closer in (self.channel.close, self.target_transport.close, self.jump_transport.close):
            try:
                closer()
            except Exception as exc:
                logger.debug("jump_session close: %s — %s", closer, exc)


def _open_direct_tcpip(
    transport: paramiko.Transport,
    dest_host: str,
    dest_port: int,
    timeout: float = 10.0,
) -> paramiko.Channel:
    """Open a direct-tcpip channel through `transport` to (dest_host, dest_port).

    The src_addr tuple is informational only — paramiko forwards it to the
    server but the LR's dropbear does not act on it. Pass a placeholder.
    """
    return transport.open_channel(
        kind="direct-tcpip",
        dest_addr=(dest_host, dest_port),
        src_addr=("supervisor", 0),
        timeout=timeout,
    )


def open_jump_shell(
    jump: JumpCreds,
    target: JumpCreds,
    term: str = "xterm",
    cols: int = 120,
    rows: int = 30,
) -> JumpSession:
    """Open SSH→LR→SSH→modem and return an interactive shell channel.

    Synchronous — call from `asyncio.to_thread`. On any failure all transports
    opened so far are closed before re-raising so we don't leak sockets.
    """
    # 1) SSH to the LR
    jump_transport, jump_observed = _open_transport(
        host=jump.host,
        port=jump.port,
        username=jump.username,
        password=jump.password,
        expected_fingerprint=jump.expected_fingerprint,
    )
    logger.debug("jump_session: connected to LR %s (fp=%s)", jump.host, jump_observed)

    target_sock: paramiko.Channel | None = None
    target_transport: paramiko.Transport | None = None
    try:
        # 2) direct-tcpip channel: LR opens TCP to (target.host, target.port)
        target_sock = _open_direct_tcpip(jump_transport, target.host, target.port)

        # 3) SSH on top of that channel — second Transport, authenticated
        target_transport = paramiko.Transport(target_sock)
        target_transport.start_client(timeout=10)
        server_key = target_transport.get_remote_server_key()
        target_observed = _fingerprint(server_key)
        if target.expected_fingerprint is None:
            logger.warning(
                "jump_session TOFU: accepting first-seen modem key %s (target=%s) — "
                "verify out-of-band and persist on Device row",
                target_observed, target.host,
            )
        elif target_observed != target.expected_fingerprint:
            raise _FingerprintMismatchError(
                f"Modem host key mismatch for {target.host}: "
                f"expected {target.expected_fingerprint}, got {target_observed}",
            )
        target_transport.auth_password(target.username, target.password, fallback=False)

        # 4) interactive PTY shell — invoke_shell allocates a pty and starts a shell
        channel = target_transport.open_session()
        channel.get_pty(term=term, width=cols, height=rows)
        channel.invoke_shell()
        return JumpSession(
            jump_transport=jump_transport,
            target_transport=target_transport,
            channel=channel,
            target_observed_fingerprint=target_observed,
        )
    except Exception:
        # Close in reverse order — channel inside target_transport, then both transports.
        if target_transport is not None:
            with contextlib.suppress(Exception):
                target_transport.close()
        elif target_sock is not None:
            with contextlib.suppress(Exception):
                target_sock.close()
        with contextlib.suppress(Exception):
            jump_transport.close()
        raise
