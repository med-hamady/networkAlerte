"""
Interactive shell on a customer-side modem (TP-Link / Huawei / ZTE).

Two endpoints work together:

1. POST /devices/{id}/shell-ticket   (HTTP, X-API-Key)
   Mints a single-use ticket bound to the device id. The ticket is required
   on the WebSocket because browsers cannot attach custom auth headers to
   `new WebSocket(...)`, so the URL itself must carry the credential.

2. WS   /devices/{id}/shell?ticket=  (WebSocket, ticket auth)
   Opens an SSH jump LR → modem (services/jump_session) and bridges the
   WebSocket frames to the interactive PTY channel.

Tickets live in an in-memory dict with a 30 s TTL — fine for a single-worker
maquette. With multiple uvicorn workers, swap the storage for Redis (the
shape is intentionally minimal so this is a one-line change).
"""

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user_or_api_key
from app.db.session import async_session_factory, get_db
from app.models.device import ClientModem
from app.services import device_service
from app.services.jump_session import JumpCreds, JumpSession, open_jump_shell

logger = logging.getLogger(__name__)

router = APIRouter()


_TICKET_TTL_SECONDS = 30


@dataclass
class _Ticket:
    device_id: int
    expires_at: float


# Single-process in-memory store. Key = ticket string, value = _Ticket.
# Cleaned on each issue + each consumption — no background task needed.
_tickets: dict[str, _Ticket] = {}


def _purge_expired(now: float) -> None:
    expired = [t for t, info in _tickets.items() if info.expires_at <= now]
    for t in expired:
        _tickets.pop(t, None)


class ShellTicket(BaseModel):
    ticket: str
    expires_in: int


@router.post(
    "/{device_id}/shell-ticket",
    response_model=ShellTicket,
    dependencies=[Depends(require_user_or_api_key)],
)
async def issue_shell_ticket(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> ShellTicket:
    """Issue a single-use ticket the browser will pass on the WebSocket URL."""
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, ClientModem):
        raise HTTPException(
            status_code=400,
            detail="Shell access is only available on client_modem devices.",
        )
    now = time.time()
    _purge_expired(now)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = _Ticket(device_id=device_id, expires_at=now + _TICKET_TTL_SECONDS)
    logger.info("Shell ticket issued for device %d", device_id)
    return ShellTicket(ticket=ticket, expires_in=_TICKET_TTL_SECONDS)


def _consume_ticket(ticket: str, device_id: int) -> bool:
    now = time.time()
    _purge_expired(now)
    info = _tickets.pop(ticket, None)
    return info is not None and info.device_id == device_id and info.expires_at > now


async def _bridge_ws_to_channel(ws: WebSocket, session: JumpSession) -> None:
    """Forward WS messages → paramiko channel. Each blocking send goes through to_thread."""
    try:
        while True:
            msg = await ws.receive()
            # WebSocket.receive() returns dicts; we accept both text frames (commands +
            # control JSON) and binary frames (raw bytes from xterm if needed).
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("text")
            if data is not None:
                # Resize control frames are sent as JSON: {"type":"resize","cols":..,"rows":..}
                if data.startswith('{"type":"resize"'):
                    try:
                        import json
                        ev = json.loads(data)
                        cols = int(ev.get("cols", 120))
                        rows = int(ev.get("rows", 30))
                        await asyncio.to_thread(session.channel.resize_pty, cols, rows)
                        continue
                    except Exception as exc:
                        logger.debug("resize parse failed: %s", exc)
                payload = data.encode("utf-8")
            else:
                payload = msg.get("bytes") or b""
            if payload:
                await asyncio.to_thread(session.channel.send, payload)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.debug("ws → channel pump ended: %s", exc)


async def _bridge_channel_to_ws(ws: WebSocket, session: JumpSession) -> None:
    """Forward paramiko channel → WS. Reads in 4 KB blocks via to_thread."""
    try:
        while True:
            data = await asyncio.to_thread(session.channel.recv, 4096)
            if not data:
                return  # remote closed
            await ws.send_bytes(data)
    except Exception as exc:
        logger.debug("channel → ws pump ended: %s", exc)


@router.websocket("/{device_id}/shell")
async def shell_websocket(websocket: WebSocket, device_id: int, ticket: str = "") -> None:
    """Interactive shell over WebSocket — auth via single-use ticket."""
    if not ticket or not _consume_ticket(ticket, device_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid ticket")
        return

    # Load device + parent LR — own session so the ws is not tied to a request DB scope.
    async with async_session_factory() as db:
        try:
            device = await device_service.get_device(db, device_id)
        except Exception:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="device lookup failed")
            return
        if not isinstance(device, ClientModem):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="not a client_modem")
            return
        if device.management_protocol != "ssh":
            await websocket.close(
                code=status.WS_1003_UNSUPPORTED_DATA,
                reason="telnet management not implemented",
            )
            return

        lr = device.lr
        if lr is None:
            await websocket.close(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="modem has no parent LR — set lr_id first",
            )
            return
        if not (lr.ssh_username and lr.ssh_password):
            await websocket.close(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="parent LR missing SSH credentials",
            )
            return
        if not (device.management_username and device.management_password):
            await websocket.close(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="modem missing management credentials",
            )
            return

        jump = JumpCreds(
            host=lr.ip_address,
            port=lr.ssh_port or 22,
            username=lr.ssh_username,
            password=lr.ssh_password,
            expected_fingerprint=lr.ssh_host_fingerprint,
        )
        target = JumpCreds(
            host=device.ip_address,
            port=device.management_port or 22,
            username=device.management_username,
            password=device.management_password,
            expected_fingerprint=device.management_host_fingerprint,
        )
        modem_id = device.id
        modem_had_fp = bool(device.management_host_fingerprint)

    await websocket.accept()
    logger.info("Shell session opening: device=%d via LR=%s", device_id, jump.host)

    try:
        session = await asyncio.to_thread(open_jump_shell, jump, target)
    except Exception as exc:
        logger.warning("Shell session open failed device=%d: %s", device_id, exc)
        await websocket.send_text(f"\r\n[supervisor] connexion échouée: {exc}\r\n")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="jump open failed")
        return

    # Persist the modem's host key on first successful TOFU.
    if not modem_had_fp and session.target_observed_fingerprint:
        async with async_session_factory() as db:
            try:
                fresh = await device_service.get_device(db, modem_id)
                if isinstance(fresh, ClientModem) and not fresh.management_host_fingerprint:
                    fresh.management_host_fingerprint = session.target_observed_fingerprint
                    await db.commit()
            except Exception as exc:
                logger.debug("Persisting modem fingerprint failed: %s", exc)

    try:
        await asyncio.gather(
            _bridge_ws_to_channel(websocket, session),
            _bridge_channel_to_ws(websocket, session),
            return_exceptions=True,
        )
    finally:
        await asyncio.to_thread(session.close)
        with contextlib.suppress(Exception):
            await websocket.close()
        logger.info("Shell session closed: device=%d", device_id)
