"""Request-level read model. Current inventory never determines historical completion."""

import datetime
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.db.session import get_db
from app.models.device import Lr
from app.models.fai_request import FaiRequest, FaiRequestEvent
from app.services import fai_audit
from app.services import fai_request_read_model as read_model
from app.services import fai_request_service as tracking

router = APIRouter()


def current(lr):
    if lr is None:
        return {"known": False}
    return {
        "known": True, "block_requested": lr.client_blocked,
        "restoration_pending": lr.unblock_pending,
        "connectivity": lr.status,
        "observed_at": tracking.iso(lr.connectivity_observed_at),
        "offline_detected_at": tracking.iso(lr.offline_detected_at),
        "online_detected_at": tracking.iso(lr.online_detected_at),
    }


def wait_view(request):
    periods = request.waiting_periods or []
    first, last = (periods[0], periods[-1]) if periods else ({}, {})
    client = next((a for a in request.actions if a["key"] == "client"), {})
    cut_at = client.get("confirmed_at") if client.get("result") == "confirmed" else None
    # Read old return-ended intervals using the recorded first start and actual
    # client confirmation; neither receipt nor inventory status supplies a date.
    ended_at = cut_at or tracking.iso(request.closed_at) or (
        last.get("ended_at") if last.get("end_reason") in ("closed", "cut") else None)
    returned_at = next((p.get("returned_at") or (p.get("ended_at") if p.get("end_reason") == "returned" else None)
                        for p in reversed(periods) if p.get("returned_at") or p.get("end_reason") == "returned"), None)
    total = 0
    if first.get("started_at"):
        start = datetime.datetime.fromisoformat(first["started_at"])
        end = datetime.datetime.fromisoformat(ended_at) if ended_at else tracking.now()
        total = max(0, int((end - start).total_seconds()))
    return {
        "periods": periods, "total_seconds": total,
        "known": bool(periods or request.identification),
        "started_at": first.get("started_at"), "ended_at": ended_at,
        "in_progress": bool(periods and not ended_at), "returned_at": returned_at,
        "awaiting_return": last.get("awaiting_return", not bool(returned_at)),
    }


def view(request, lr):
    can_resume = request.active and lr is not None
    closed_reason = request.closed_reason or ("Fiche supprimée : aucune reprise possible." if lr is None else None)
    confirmed = [a for a in request.actions if a["result"] == "confirmed"]
    remaining = [a for a in request.actions if a.get("required", True) and a["result"] != "confirmed"]
    if request.completed_at:
        summary = ("Filtre WhatsApp sur le LR confirmé." if request.mode == "whatsapp_only"
                   else "Coupure du port LAN du LR confirmée.")
        if any(a["key"] == "router_cleanup" for a in confirmed):
            summary += " Retrait ou absence du blocage de secours sur le routeur confirmé."
    else:
        summary = ""
        if any(a["key"] == "router" for a in confirmed):
            summary += "Blocage de secours sur le routeur confirmé. "
        if any(a["key"] == "client" for a in confirmed):
            summary += "Action sur le LR confirmée. "
        if lr and lr.status == "down" and any(a["key"] == "client" for a in remaining):
            summary += "LR non connecté : la coupure sur cet appareil n'est pas confirmée. "
            if can_resume:
                summary += "En attente du retour à la connexion pour reprendre la coupure. "
        else:
            obstacle = next((a.get("reason") for a in remaining if a["result"] in ("failed", "unknown")), None)
            if obstacle:
                summary += (obstacle[:150] + "…" if len(obstacle) > 150 else obstacle) + " "
        short_actions = {"client": "coupure LR" if request.mode == "full" else "filtre WhatsApp LR",
                         "router": "blocage de secours sur le routeur",
                         "router_cleanup": "retrait du blocage de secours après la coupure LR",
                         "mode_cleanup": "ouverture du port LAN"}
        summary += "Reste à confirmer : " + "; ".join(
            short_actions.get(a["key"], a["operation"]) for a in remaining) + "."
        if not can_resume:
            summary += f" Reprise désactivée : {closed_reason or 'Demande clôturée.'}"
    identification = request.identification or {}
    wait = wait_view(request)
    client = next((a for a in request.actions if a["key"] == "client"), {})
    cut_at = client.get("confirmed_at") if client.get("result") == "confirmed" else None
    stage = ("completed" if request.completed_at else "closed" if not can_resume
             else "waiting" if wait["in_progress"] and wait["awaiting_return"] else "finalizing" if cut_at
             else "to_cut" if wait["returned_at"] else "attention" if client.get("result") in ("failed", "unknown")
             else "pending")
    return {
        "id": request.request_key, "received_at": tracking.iso(request.received_at),
        "completed_at": tracking.iso(request.completed_at),
        "status": "completed" if request.completed_at else "incomplete",
        "active": can_resume,
        "closed_at": tracking.iso(request.closed_at), "closed_reason": closed_reason,
        "name": request.name, "mac": request.mac, "site": request.site,
        "client_id": identification.get("client_id"),
        "client_name": identification.get("client_name") or request.name,
        "contact_name": identification.get("contact_name"),
        "contact_phone": identification.get("contact_phone"),
        "cut_at": cut_at, "waiting": wait, "stage": stage,
        "source": request.source, "agent": request.agent,
        "mode": request.mode, "reason": request.reason,
        "actions": request.actions, "summary": summary,
        "remaining": [a["operation"] for a in remaining],
        "legacy": False, "current": current(lr),
    }


def legacy_view(entry, index, lr):
    # Old ok=True may only mean fallback coverage or command accepted; it cannot
    # prove all device actions. Preserve the old statement but do not manufacture
    # completion, receipt time (old log time is after execution), or device times.
    digest = hashlib.sha256(f"{index}:{entry}".encode()).hexdigest()[:24]
    return {
        "id": "legacy-" + digest, "received_at": None,
        "logged_at": entry["timestamp"], "completed_at": None,
        "status": "unverified", "active": False, "closed_at": None,
        "closed_reason": "Historique sans identité de demande ni confirmations par appareil.",
        "name": entry["name"], "mac": entry["mac"], "site": None,
        "client_id": None, "client_name": entry["name"], "contact_name": None, "contact_phone": None,
        "cut_at": None, "waiting": None, "stage": "legacy",
        "source": entry["source"], "agent": entry["user"], "mode": entry["mode"],
        "reason": None, "actions": [dict(
            tracking.action("client", f"LR {entry['mac'] or 'inconnu'}", "Vérifier la coupure historique"),
            result="unknown", reason="Confirmation par appareil indisponible dans l'ancien journal.",
        )],
        "summary": "Résultat historique non confirmé par appareil. " + entry["message"],
        "remaining": ["Vérifier les preuves historiques ; aucune nouvelle coupure depuis cette ligne."],
        "legacy": True, "current": current(lr),
        "events": [{"timestamp": entry["timestamp"], "kind": "legacy", "target": entry["mac"],
                    "result": "unknown", "message": entry["message"]}],
    }


@router.get("")
async def get_requests(
    source: str | None = None, search: str | None = None,
    status: str | None = Query(None, pattern="^(completed|incomplete|unverified)$"),
    situation: Literal["restored", "deleted"] | None = None,
    start: datetime.date | None = None, end: datetime.date | None = None,
    limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    if (start is None) != (end is None) or (start and end < start):
        raise HTTPException(422, "Fournir une plage de jours UTC valide, avec les deux bornes.")
    schema = await read_model.read_schema(db)
    requests = await read_model.read_requests(db, schema)
    # Journal statistics cover the whole readable file, even when filters select
    # no rows. A missing or empty local file cannot prove no company requests.
    entries, journal_stats = fai_audit.read_entries(limit=None, source=source, start=start, end=end)
    legacy_journal = journal_stats["total"] > 0
    # A MAC and a second-resolution timestamp are not a request identity. A
    # separate legacy operation may share both with a modern request. Match
    # only an explicit marker or the deterministic key used by the importer.
    structured_keys = {r.request_key for r in requests}
    legacy_entries = [e for e in entries
                      if e["action"] in ("BLOCK", "IDENT_KO")
                      and e["source"] != "enforce"
                      and (e.get("request_id") or fai_audit.legacy_request_key(
                          e["timestamp"], e["mac"], e["action"])) not in structured_keys]
    devices = await read_model.read_lrs(db, schema) if requests or legacy_entries else []
    by_id = {lr.id: lr for lr in devices}
    by_mac = {(lr.mac_address or "").lower(): lr for lr in devices}
    rows = [view(r, by_id.get(r.device_id)) for r in requests]
    rows.extend(legacy_view(e, i, by_mac.get((e["mac"] or "").lower()))
                for i, e in enumerate(legacy_entries))
    query = (search or "").strip().lower()
    rows = [r for r in rows
            if (not source or r["source"].lower() == source.lower())
            and (not query or query in " ".join(str(r.get(k) or "") for k in (
                "name", "client_name", "client_id", "contact_name", "contact_phone", "mac", "agent", "site", "source", "reason")).lower())
            and (not start or start.isoformat() <= (r["received_at"] or r.get("logged_at", ""))[:10] <= end.isoformat())]
    # These filters describe today's inventory/intent, never the historic result.
    if situation == "restored":
        rows = [r for r in rows if r["current"]["known"] and not r["current"]["block_requested"]]
    elif situation == "deleted":
        rows = [r for r in rows if not r["current"]["known"]]
    counts = {"total": len(rows), "completed": sum(r["status"] == "completed" for r in rows)}
    counts["incomplete"] = sum(r["status"] == "incomplete" for r in rows)
    counts["unverified"] = sum(r["status"] == "unverified" for r in rows)
    rows = [r for r in rows if not status or r["status"] == status]
    rows.sort(key=lambda r: r["received_at"] or r.get("logged_at", ""), reverse=True)
    history_available = schema.request_history or legacy_journal
    notice = None
    if not schema.request_history:
        notice = ("Le suivi des demandes n'est pas disponible dans cette base. "
                  "Seules les éventuelles entrées de l'ancien journal accessible à cette instance sont affichées.")
        if not legacy_journal:
            notice += " Aucun journal historique lisible n'est disponible ici ; le nombre de demandes de l'entreprise est inconnu."
    return {"entries": rows[offset:offset + limit], "counts": counts if history_available else None,
            "matching": len(rows), "limit": limit, "offset": offset,
            "capabilities": {"request_history": schema.request_history,
                             "event_history": schema.event_history,
                             "connectivity_timestamps": schema.connectivity_timestamps,
                             "legacy_journal": legacy_journal},
            "history_available": history_available, "notice": notice}


@router.get("/{request_key}/events")
async def get_events(request_key: str, db: AsyncSession = Depends(get_db)):
    schema = await read_model.read_schema(db)
    if not schema.event_history:
        return {"events": [], "notice": "Historique détaillé indisponible pour cette connexion."}
    result = await read_model.read_request_events(db, request_key)
    if result is None:
        raise HTTPException(404, "Demande introuvable.")
    actions, events = result
    return {"events": [{"timestamp": tracking.iso(e["timestamp"]), "target": e["target"],
                        "action_key": next((a["key"] for a in actions if a["target"] == e["target"]), None),
                        "kind": e["kind"], "result": e["result"], "message": e["message"]} for e in events],
            "notice": None}


@router.post("/{request_key}/cancel", dependencies=[Depends(require_permission("fai.block"))])
async def cancel_request(request_key: str, db: AsyncSession = Depends(get_db)):
    request = (await db.execute(select(FaiRequest).where(
        FaiRequest.request_key == request_key,
    ))).scalar_one_or_none()
    if request is None:
        raise HTTPException(404, "Demande introuvable.")
    device_id = request.device_id
    await db.commit()
    if device_id is None:
        raise HTTPException(409, "Fiche supprimée ; aucune reprise automatique possible.")
    async with tracking.device_lock(db, device_id):
        await db.refresh(request, with_for_update=True)
        if request.active and request.completed_at is None:
            request.active = False
            request.closed_at = tracking.now()
            request.closed_reason = "Demande annulée : aucune nouvelle tentative de coupure."
            tracking.end_wait(db, request, request.closed_at, "closed")
            tracking.event(db, request, "closed", request.closed_reason)
            lr = await db.get(Lr, device_id, populate_existing=True)
            if lr:
                lr.client_blocked = False
                lr.client_blocked_at = None
                lr.client_blocked_reason = None
            await db.commit()
    return {"id": request_key, "status": "completed" if request.completed_at else "incomplete",
            "active": request.active}
