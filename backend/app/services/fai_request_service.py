"""Request identity, confirmed device outcomes and cancellation-safe execution.

The lock is shared by API and scheduler processes and survives their commits.
It orders restoration after an already running cut; a queued old retry reloads
the intent AFTER acquiring the lock and cannot cut again after restoration.
"""

import asyncio
import datetime
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.device import Lr
from app.models.fai_request import FaiRequest, FaiRequestEvent
from app.services import fai_audit, mikrotik_service

_execution: ContextVar[tuple | None] = ContextVar("fai_request_execution", default=None)
_lock_engines: dict[AsyncEngine, AsyncEngine] = {}
# Five production processes (four API workers + heavy scheduler): at most
# 15 dedicated lock connections, within the existing PostgreSQL budget.
_lock_concurrency = asyncio.Semaphore(3)


def now():
    return datetime.datetime.now(datetime.UTC)


def iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or datetime.UTC).isoformat()


def action(key, target, operation, required=True):
    return {"key": key, "target": target, "operation": operation,
            "result": "pending", "attempted_at": None, "confirmed_at": None,
            "reason": "Aucune exécution confirmée.", "required": required}


def router_target():
    return f"Routeur de cœur · {get_settings().mikrotik_host}"


def event(session, request, kind, message, target=None, result=None, timestamp=None):
    session.add(FaiRequestEvent(
        request_id=request.id, timestamp=timestamp or now(), kind=kind,
        target=target, result=result, message=message,
    ))


async def active_request(session, device_id, *, lock=False):
    query = select(FaiRequest).where(
        FaiRequest.device_id == device_id, FaiRequest.active.is_(True),
    ).order_by(FaiRequest.id.desc()).limit(1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def close_active(session, device_id, reason):
    result = await session.execute(select(FaiRequest).where(
        FaiRequest.device_id == device_id, FaiRequest.active.is_(True),
    ).with_for_update().execution_options(populate_existing=True))
    for request in result.scalars():
        request.active = False
        request.closed_at = now()
        request.closed_reason = reason
        end_wait(session, request, request.closed_at, "closed")
        # completed_at and confirmed outcomes are historical and never cleared.
        event(session, request, "closed", reason)


async def begin(session, lr, reason, mode, source, agent, received_at=None,
                contact_name=None, contact_phone=None):
    if lr.topology_mode != "bridge" and lr.ssh_username and lr.ssh_password:
        await close_active(session, lr.id, "Remplacée par une nouvelle demande de coupure.")
    actions = [action("client", f"LR {lr.name} · {lr.mac_address or 'MAC inconnue'}",
                      "Filtre WhatsApp uniquement" if mode == "whatsapp_only"
                      else f"Fermer le port LAN {lr.lan_interface}")]
    if mikrotik_service.is_enabled() and (lr.router_blocked or mode == "whatsapp_only"):
        actions.append(action("router_cleanup", router_target(),
                              "Vérifier le retrait du blocage de secours après la coupure LR",
                              required=mode == "whatsapp_only"))
    if mode == "whatsapp_only":
        actions.append(action("mode_cleanup", f"LR {lr.name}", "Vérifier que le port LAN est ouvert pour autoriser WhatsApp"))
    request = FaiRequest(
        device_id=lr.id, request_key=str(uuid.uuid4()), mac=lr.mac_address,
        name=lr.name, site=lr.site, source=source, agent=agent,
        mode=mode, reason=reason, received_at=received_at or now(), active=True, actions=actions,
        identification={"client_id": getattr(lr, "uisp_crm_client_id", None),
                        "client_name": getattr(lr, "uisp_crm_client_name", None) or lr.name,
                        "contact_name": contact_name, "contact_phone": contact_phone},
        waiting_periods=[],
    )
    session.add(request)
    await session.flush()
    event(session, request, "received", "Demande reçue : actions à distance via le réseau.",
          timestamp=request.received_at)
    if lr.status == "down":
        event(session, request, "offline", "LR non connecté ; extinction physique non confirmée.",
              f"LR {lr.name}", timestamp=lr.offline_detected_at or request.received_at)
    # The caller commits this receipt WITH the accepted device intent before
    # its first network operation, so a crash cannot leave an orphaned queue.
    return request


def waiting(request):
    periods = request.waiting_periods or []
    # Old intervals ended on return. Return is evidence of reachability,
    # not completion: keep their original start until a confirmed cut/closure.
    return bool(periods and not request.completed_at
                and periods[-1].get("end_reason") not in ("cut", "closed"))


def start_wait(session, request, timestamp):
    if waiting(request):
        periods = [dict(p) for p in request.waiting_periods]
        periods[-1]["awaiting_return"] = True
        request.waiting_periods = periods
        return
    request.waiting_periods = [*(request.waiting_periods or []),
                               {"started_at": iso(timestamp), "ended_at": None,
                                "end_reason": None, "returned_at": None, "awaiting_return": True}]
    event(session, request, "waiting_started",
          "En attente du retour de l’équipement pour effectuer la coupure.", timestamp=timestamp)


def return_observed(request, timestamp):
    if not waiting(request):
        return False
    periods = [dict(p) for p in request.waiting_periods]
    last = periods[-1]
    if last.get("awaiting_return") is False:
        return False
    if not last.get("returned_at"):
        last["returned_at"] = last.get("ended_at") if last.get("end_reason") == "returned" else iso(timestamp)
    last["awaiting_return"] = False
    request.waiting_periods = periods
    return True


def end_wait(session, request, timestamp, reason="cut"):
    if not waiting(request):
        return False
    periods = [dict(p) for p in request.waiting_periods]
    last = periods[-1]
    if last.get("end_reason") == "returned" and not last.get("returned_at"):
        last["returned_at"] = last.get("ended_at")
    last.update(ended_at=iso(timestamp), end_reason=reason)
    request.waiting_periods = periods
    event(session, request, "waiting_ended",
          "Attente terminée : coupure de l’équipement confirmée." if reason == "cut"
          else "Attente arrêtée : la demande n’est plus active.", timestamp=timestamp)
    return True


def confirm_completion(session, request, timestamp):
    client = next((a for a in request.actions if a["key"] == "client"), {})
    ready = client.get("result") == "confirmed" and (
        request.mode == "full" or all(a["result"] == "confirmed" for a in request.actions if a.get("required", True)))
    if request.active and request.completed_at is None and ready:
        request.completed_at = timestamp
        event(session, request, "completed", "Coupure demandée sur l’équipement confirmée.", timestamp=timestamp)


async def connection_observed():
    """A session to the expected client is evidence of return, even if the
    later cut command fails. It is not evidence that the cut succeeded.
    """
    context = _execution.get()
    if context is None:
        return
    session, request = context
    stamp = now()
    if isinstance(getattr(session, "bind", None), AsyncEngine):
        with session.no_autoflush:
            await session.refresh(request, with_for_update=True)
    if request.active and request.completed_at is None and return_observed(request, stamp):
        event(session, request, "online", "Retour détecté lors de la connexion à l’équipement.", timestamp=stamp)
        await session.commit()


async def record(key, result, message, operation=None, target=None, required=True, unavailable=False):
    context = _execution.get()
    if context is None:
        return
    session, request = context
    timestamp = now()
    if isinstance(getattr(session, "bind", None), AsyncEngine):
        # Ping observations and SSH outcomes can arrive in different workers.
        # Read under a row lock so neither loses a persisted wait transition.
        with session.no_autoflush:
            await session.refresh(request, with_for_update=True)
    if not request.active or (request.completed_at is not None and key not in ("router_cleanup", "mode_cleanup")):
        return
    actions = [dict(a) for a in request.actions]
    item = next((a for a in actions if a["key"] == key), None)
    if item is None:
        if request.mode == "full" and key in ("router", "router_cleanup"):
            required = False
        item = action(key, target or router_target(), operation or "Blocage de secours par MAC", required)
        actions.append(item)
    elif item["result"] == "confirmed":
        # A later reassertion must not change when this action first succeeded.
        return
    if key == "client" and unavailable:
        start_wait(session, request, timestamp)
    elif key == "client" and result == "confirmed" and return_observed(request, timestamp):
        # SSH success also proves reachability if no ping return was observed.
        # This is a detection timestamp, never a guessed physical power-on time.
        event(session, request, "online", "Retour constaté lors de la confirmation de la coupure.",
              timestamp=timestamp)
    if key == "client" and result == "confirmed":
        end_wait(session, request, timestamp, "cut")
    item["result"] = result
    item["reason"] = None if result == "confirmed" else message
    item["message"] = message
    if result == "attempting":
        item["attempted_at"] = iso(timestamp)
    item["confirmed_at"] = iso(timestamp) if result == "confirmed" else None
    request.actions = actions
    event(session, request, "attempt" if result == "attempting" else "outcome",
          message, item["target"], result, timestamp)
    confirm_completion(session, request, timestamp)
    await session.commit()


def needs_action(key):
    context = _execution.get()
    if context is None:
        return False
    _, request = context
    return (request.active and request.completed_at is None
            and (any(a["key"] == key and a["result"] != "confirmed" for a in request.actions)
                 or key == "router" and not any(a["key"] == key for a in request.actions)))


async def connectivity_event(session, device, reachable, timestamp):
    if not isinstance(device, Lr):
        return
    request = await active_request(session, device.id, lock=True)
    if request and request.completed_at is None:
        if reachable:
            returned = return_observed(request, timestamp)
            if not returned and device.status == "up" and device.connectivity_observed_at is not None:
                return
        elif waiting(request):
            start_wait(session, request, timestamp)
        event(session, request, "online" if reachable else "offline",
              ("Retour à la connexion détecté." if device.status == "down"
               else "Connexion observée.") if reachable
              else "Perte de connexion détectée ; extinction physique non confirmée.",
              f"LR {device.name}", timestamp=timestamp)


async def finish(session, request):
    confirm_completion(session, request, now())
    await session.commit()


def observe_connectivity(device, reachable, timestamp):
    """Called by actual ping observations, not by an HTTP page read."""
    previous = device.status
    if reachable:
        if previous == "down":
            device.online_detected_at = timestamp
        device.offline_detected_at = None
    elif previous != "down" or device.offline_detected_at is None:
        device.offline_detected_at = timestamp
    device.connectivity_observed_at = timestamp


@asynccontextmanager
async def device_lock(session, device_id):
    engine = getattr(session, "bind", None)
    if not isinstance(engine, AsyncEngine):
        # Existing unit tests use fake sessions and devices; no external I/O.
        yield
        return
    if engine.dialect.name != "postgresql":
        # SQLite is used only by isolated tests, which supply their own lock.
        yield
        return
    # A separate, unpooled connection avoids starving the normal session pool
    # when concurrent workers all hold locks while persisting their outcomes.
    locking_engine = _lock_engines.get(engine)
    if locking_engine is None:
        locking_engine = create_async_engine(engine.url, poolclass=NullPool)
        _lock_engines[engine] = locking_engine
    async with _lock_concurrency, locking_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        try:
            await connection.execute(text("SELECT pg_advisory_lock(4604233, :id)"), {"id": device_id})
        except BaseException:
            await connection.invalidate()
            raise
        try:
            yield
        finally:
            try:
                await asyncio.shield(connection.execute(
                    text("SELECT pg_advisory_unlock(4604233, :id)"), {"id": device_id},
                ))
            except BaseException:
                # Never return a still-locked connection to the pool.
                await connection.invalidate()
                raise


def serialized(fn):
    @wraps(fn)
    async def run(session, lr_or_id, *args, **kwargs):
        lr_id = lr_or_id if isinstance(lr_or_id, int) else lr_or_id.id
        async with device_lock(session, lr_id):
            real = isinstance(lr_or_id, Lr) or isinstance(getattr(session, "bind", None), AsyncEngine)
            if not real:
                return await fn(session, lr_or_id, *args, **kwargs)
            if isinstance(lr_or_id, Lr):
                await session.refresh(lr_or_id)  # discard pre-lock stale intent
            elif isinstance(lr_or_id, int):
                await session.get(Lr, lr_id, populate_existing=True)
            if fn.__name__ == "block_client":
                mode = kwargs.get("mode") or (args[1] if len(args) > 1 else None)
                mode = mode or get_settings().client_block_default_mode
                mode = mode if mode in ("full", "whatsapp_only") else "full"
                request = await begin(session, lr_or_id, args[0] if args else kwargs.get("reason"), mode,
                                      kwargs.get("source", "dashboard"), kwargs.get("user"),
                                      kwargs.get("received_at"), kwargs.get("contact_name"),
                                      kwargs.get("contact_phone"))
            elif fn.__name__ == "unblock_client":
                await close_active(session, lr_id, "Reprise annulée : rétablissement demandé.")
                # Commit together with the cleared device intent in the body:
                # a crash must not leave an inactive request with a live cut.
                request = None
            else:
                request = await active_request(session, lr_id)
                await session.commit()
            token = _execution.set((session, request) if request else None)
            try:
                result = await fn(session, lr_or_id, *args, **kwargs)
                if request:
                    if fn.__name__ == "block_client" and all(
                            a["result"] == "pending" for a in request.actions):
                        await record("client", "failed", result[1])
                        request.active = False  # no intent accepted, no automatic retry
                        request.closed_at = now()
                        request.closed_reason = "Demande refusée avant exécution."
                    await finish(session, request)
                    if fn.__name__ == "block_client":
                        lr_or_id._fai_request = request
                        fai_audit.log_action(
                            "IDENT_KO" if result[1].startswith("Identité refusée :") else "BLOCK",
                            ok=result[0], mac=request.mac, name=request.name,
                            mode=request.mode, source=request.source, user=request.agent,
                            message=result[1], evidence=result[2], request_id=request.request_key,
                        )
                return result
            except Exception as exc:
                if request:
                    await session.rollback()
                    await session.refresh(request)
                    keys = [a["key"] for a in request.actions if a["result"] == "attempting"]
                    for key in keys or ["client"]:
                        await record(key, "unknown", f"Résultat non confirmé : {type(exc).__name__}.")
                raise
            finally:
                _execution.reset(token)
    return run
