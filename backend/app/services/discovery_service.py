"""
Discovery service — reconciles peers reported by a Rocket against the LR table.

Single entry point: `reconcile_peers(session, parent, peers)`.

The operator only registers Rocket APs (LTU or airMAX). Each polling cycle, the
Rocket reports the list of CPE/LR peers connected to its radio. This service
turns that ephemeral list into persistent Lr rows and keeps them in sync when
the network topology changes:

  - new LR detected               → create Lr(auto_discovered=True), open AT_LR_DISCOVERED
  - same MAC, different IP        → update IP, open AT_LR_IP_CHANGED
  - same MAC, different parent    → update rocket_id, open AT_LR_REASSIGNED
  - hostname / firmware drifted   → silent update (logged)

The MAC address is the stable identifier. IP-based matching is only used as a
fallback for legacy devices created before mac_address was tracked.

The function is data-source agnostic: peers can come from the LTU HTTP API or
from the airOS UBNT SNMP station table — anything that produces a list of
PeerInfo dicts works.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import (
    AT_LR_DISCOVERED,
    AT_LR_IP_CHANGED,
    AT_LR_REASSIGNED,
    Severity,
)
from app.models.alert import Alert
from app.models.device import Device, Lr, Rocket
from app.schemas.device import normalize_mac
from app.services import incident_service, notification_service

logger = logging.getLogger(__name__)


# Credentials SSH standard pour toutes les LR côté client A2.
# Appliqués automatiquement à chaque LR auto-découverte ; un opérateur peut
# toujours les écraser ensuite via PUT /api/v1/devices/{id}.
_LR_DEFAULT_SSH_USERNAME = "ubnt"
_LR_DEFAULT_SSH_PASSWORD = "A2HQ@87654321"  # noqa: S105


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class PeerInfo(TypedDict, total=False):
    """Normalised peer descriptor produced by data-source adapters.

    All fields are optional except that at least one of `mac` or `mgmt_ip` must
    be set — without an identifier the peer cannot be reconciled.
    """
    mac:      str | None  # MUST be normalised by caller (or via normalize_mac)
    mgmt_ip:  str | None
    hostname: str | None
    model:    str | None
    firmware: str | None


@dataclass
class ReconcileResult:
    """Outcome of a reconciliation pass — used by callers for logging/metrics."""

    matched: list[Lr] = field(default_factory=list)
    created: list[Lr] = field(default_factory=list)
    ip_changed: list[tuple[Lr, str]] = field(default_factory=list)   # (device, old_ip)
    reassigned: list[tuple[Lr, int | None]] = field(default_factory=list)  # (device, old_rocket_id)


# ---------------------------------------------------------------------------
# Naming and normalisation
# ---------------------------------------------------------------------------

def _generate_device_name(peer: PeerInfo, fallback_index: int) -> str:
    """Build a friendly name for an auto-discovered device.

    Priority: hostname → "LR <last 6 MAC bytes>" → "LR <ip>" → generic.
    """
    hostname = peer.get("hostname")
    if hostname:
        return hostname
    mac = peer.get("mac")
    if mac:
        return f"LR {mac.replace(':', '')[-6:].upper()}"
    ip = peer.get("mgmt_ip")
    if ip:
        return f"LR {ip}"
    return f"LR auto #{fallback_index}"


def _normalised_mac(peer: PeerInfo) -> str | None:
    """Best-effort MAC normalisation; returns None if the peer has no usable MAC."""
    raw = peer.get("mac")
    if not raw:
        return None
    try:
        return normalize_mac(raw)
    except ValueError:
        return None


def _infer_model_variant(peer: PeerInfo, parent: Device) -> str:
    """Pick the right LR model_variant from the peer's reported model string.

    Ubiquiti firmware reports model identifiers in `peers[i].common.identification.model`.
    Examples seen on real devices:
      LTU family   : "LTU‑LR", "LTU LR", "LTU-Pro", "LTU-Instant", "LTU-Lite"
      airMAX family: "LBE-M5-23" / "LiteBeam M5", "LBE-5AC-Gen2" / "LiteBeam 5AC",
                     "NBE-M5-19", "NSM5"

    Unknown strings fall back to the parent Rocket's radio_tech:
      LTU parent → ltu_lr, airMAX parent → litebeam_5ac (most common Litebeam).
    The raw string is logged so operators can extend this mapping later.
    """
    raw = (peer.get("model") or "").strip()
    if not raw:
        return "ltu_lr" if not isinstance(parent, Rocket) or parent.radio_tech != "airmax" else "litebeam_5ac"

    norm = raw.lower().replace("-", " ").replace("_", " ")

    # LTU family — LTU Rockets' peers
    if "ltu" in norm:
        if "instant" in norm or "pro" in norm:
            return "ltu_instant"
        if "lite" in norm:
            return "ltu_lite"
        return "ltu_lr"

    # airMAX Litebeam family
    if "lbe" in norm or "litebeam" in norm:
        if "m5" in norm:
            return "litebeam_m5"
        # LBE-5AC-Gen2, "LiteBeam 5AC", etc.
        return "litebeam_5ac"

    # Unknown — default by parent radio tech, log for future mapping extension
    parent_kind = "airmax" if (isinstance(parent, Rocket) and parent.radio_tech == "airmax") else "ltu"
    fallback = "litebeam_5ac" if parent_kind == "airmax" else "ltu_lr"
    logger.warning(
        "Discovery: unknown peer model %r under %s Rocket '%s' — defaulting to %s. "
        "Extend _infer_model_variant if this becomes recurrent.",
        raw, parent_kind, parent.name if hasattr(parent, "name") else "?", fallback,
    )
    return fallback


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

async def _find_lr_by_mac(session: AsyncSession, mac: str) -> Lr | None:
    res = await session.execute(select(Lr).where(Lr.mac_address == mac))
    return res.scalar_one_or_none()


async def _find_lr_by_ip(session: AsyncSession, ip: str) -> Lr | None:
    """Lookup by IP, restricted to LRs.

    Without the type guard we'd match a Rocket whose IP collides with a peer's
    mgmt_ip (rare but possible on misconfigured networks).
    """
    res = await session.execute(select(Lr).where(Lr.ip_address == ip))
    return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Incident emission for lifecycle events
# ---------------------------------------------------------------------------

async def _emit_lifecycle_event(
    session: AsyncSession,
    device: Device,
    alert_type: str,
    title: str,
    severity: str,
    description: str,
) -> None:
    """Open a lifecycle incident and send the notification.

    Lifecycle incidents (discovered / ip_changed / reassigned) auto-resolve
    immediately — they are point-in-time events, not ongoing conditions.
    """
    incident, is_new = await incident_service.open_incident(
        session, device,
        title=title,
        severity=severity,
        description=description,
        alert_type=alert_type,
    )
    if not is_new:
        return  # already-open lifecycle event — no double notification

    ok = await notification_service.notify_incident_opened(device, incident)
    now = datetime.datetime.now(datetime.UTC)
    session.add(Alert(
        incident_id=incident.id,
        message=f"{title} — {device.name}",
        status="sent" if ok else "failed",
        sent_at=now if ok else None,
    ))

    # Auto-resolve the lifecycle incident — it is an event, not a state.
    incident.status = "resolved"
    incident.resolved_at = now


# ---------------------------------------------------------------------------
# Per-peer reconciliation
# ---------------------------------------------------------------------------

async def _reconcile_single_peer(
    session: AsyncSession,
    parent: Rocket,
    peer: PeerInfo,
    fallback_index: int,
    result: ReconcileResult,
) -> Lr | None:
    """Reconcile one peer entry — create or update the corresponding Lr row."""
    mac = _normalised_mac(peer)
    ip = peer.get("mgmt_ip")
    if mac is None and ip is None:
        logger.debug(
            "Discovery: peer reported by %s has neither MAC nor IP — skipped",
            parent.name,
        )
        return None

    now = datetime.datetime.now(datetime.UTC)

    # ── 1. Lookup ─────────────────────────────────────────────────────────
    device: Lr | None = None
    if mac:
        device = await _find_lr_by_mac(session, mac)
    if device is None and ip:
        # Fallback for legacy LR devices created before MAC tracking existed.
        # Once matched, the MAC gets pinned so future cycles use the fast path.
        device = await _find_lr_by_ip(session, ip)
        if device and mac and not device.mac_address:
            device.mac_address = mac
            logger.info(
                "Discovery: pinned MAC %s on legacy LR '%s' (matched by IP)",
                mac, device.name,
            )

    # ── 2. Create branch ──────────────────────────────────────────────────
    if device is None:
        # IP is required because Device.ip_address is NOT NULL + unique. A peer
        # whose Rocket reports only the MAC will be retried next cycle when the
        # IP becomes available (e.g. once DHCP completes).
        if not ip:
            logger.debug(
                "Discovery: peer %s vu sur '%s' sans mgmt_ip — création différée",
                mac or "?", parent.name,
            )
            return None

        # Avoid creating a duplicate when the IP is already used by any other
        # device row (operator-created Rocket, Switch, etc.).
        existing_any = await session.execute(
            select(Device).where(Device.ip_address == ip)
        )
        if existing_any.scalar_one_or_none() is not None:
            logger.warning(
                "Discovery: IP %s déjà attribuée — création du LR (mac=%s) abandonnée",
                ip, mac or "?",
            )
            return None

        model_variant = _infer_model_variant(peer, parent)
        device = Lr(
            name=_generate_device_name(peer, fallback_index),
            ip_address=ip,
            status="unknown",
            mac_address=mac,
            hostname=peer.get("hostname"),
            firmware_version=peer.get("firmware"),
            model_variant=model_variant,
            rocket_id=parent.id,
            auto_discovered=True,
            first_discovered_at=now,
            last_discovered_at=now,
            # Toutes les LR côté client partagent les mêmes credentials SSH
            # (déploiement standardisé A2). La sonde transit en a besoin dès
            # la découverte — sans cela, le job lr_transit_probe skip le device.
            ssh_username=_LR_DEFAULT_SSH_USERNAME,
            ssh_password=_LR_DEFAULT_SSH_PASSWORD,
            ssh_port=22,
        )
        session.add(device)
        await session.flush()  # populate device.id for the lifecycle incident

        result.created.append(device)
        logger.info(
            "Discovery: NEW LR auto-créé '%s' (mac=%s ip=%s variant=%s parent=%s)",
            device.name, mac, ip, model_variant, parent.name,
        )
        await _emit_lifecycle_event(
            session, device,
            alert_type=AT_LR_DISCOVERED,
            title=f"Nouveau LR détecté : {device.name}",
            severity=Severity.INFO,
            description=(
                f"Le LR '{device.name}' (MAC={mac or 'inconnue'}, IP={ip or 'inconnue'}) "
                f"a été automatiquement enregistré comme peer du Rocket '{parent.name}'. "
                f"Modèle: {peer.get('model') or 'inconnu'} (variant={model_variant}) · "
                f"Firmware: {peer.get('firmware') or 'inconnu'}."
            ),
        )
        return device

    # ── 3. Update branch — device already in DB ───────────────────────────
    result.matched.append(device)
    device.last_discovered_at = now
    if device.first_discovered_at is None:
        device.first_discovered_at = now

    # IP change (only if MAC matched — we trust the MAC as identity)
    if mac and ip and device.ip_address != ip:
        old_ip = device.ip_address
        device.ip_address = ip
        result.ip_changed.append((device, old_ip))
        logger.warning(
            "Discovery: LR '%s' (MAC=%s) — IP changée %s → %s",
            device.name, mac, old_ip, ip,
        )
        await _emit_lifecycle_event(
            session, device,
            alert_type=AT_LR_IP_CHANGED,
            title=f"Changement d'IP : {device.name} ({old_ip} → {ip})",
            severity=Severity.WARNING,
            description=(
                f"Le LR '{device.name}' (MAC={mac}) a changé d'adresse IP : "
                f"{old_ip} → {ip}. Vérifier la cohérence DHCP/configuration. "
                f"Les anciennes sessions SSH/API pointant vers {old_ip} sont obsolètes."
            ),
        )

    # Parent reassignment (LR reported by a different Rocket than its current parent)
    if device.rocket_id != parent.id:
        old_parent_id = device.rocket_id
        device.rocket_id = parent.id
        result.reassigned.append((device, old_parent_id))

        old_parent_name = "aucun"
        if old_parent_id is not None:
            old_parent_res = await session.execute(
                select(Rocket).where(Rocket.id == old_parent_id)
            )
            old_parent_dev = old_parent_res.scalar_one_or_none()
            if old_parent_dev is not None:
                old_parent_name = old_parent_dev.name

        logger.warning(
            "Discovery: LR '%s' a basculé : %s → %s",
            device.name, old_parent_name, parent.name,
        )
        await _emit_lifecycle_event(
            session, device,
            alert_type=AT_LR_REASSIGNED,
            title=f"LR rebasculé : {device.name} ({old_parent_name} → {parent.name})",
            severity=Severity.WARNING,
            description=(
                f"Le LR '{device.name}' (MAC={mac or 'inconnue'}) est désormais rapporté "
                f"par le Rocket '{parent.name}' alors qu'il était précédemment rattaché "
                f"à '{old_parent_name}'. Vérifier si ce bascule est volontaire ou s'il "
                f"révèle une panne sur le lien d'origine."
            ),
        )

    # Silent updates — hostname / firmware drifts get logged but no incident.
    # The peer's `model` string is mapped to model_variant only at creation;
    # we don't re-infer on update to avoid silently flipping a manually
    # adjusted variant.
    new_hostname = peer.get("hostname")
    if new_hostname and new_hostname != device.hostname:
        logger.info(
            "Discovery: LR '%s' hostname mis à jour : %r → %r",
            device.name, device.hostname, new_hostname,
        )
        device.hostname = new_hostname

    new_firmware = peer.get("firmware")
    if new_firmware and new_firmware != device.firmware_version:
        logger.info(
            "Discovery: LR '%s' firmware mis à jour : %r → %r",
            device.name, device.firmware_version, new_firmware,
        )
        device.firmware_version = new_firmware

    return device


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def reconcile_peers(
    session: AsyncSession,
    parent: Rocket,
    peers: list[PeerInfo],
) -> ReconcileResult:
    """Reconcile a Rocket's peer list with the lrs table.

    Parameters
    ----------
    session : AsyncSession
        Live transaction. The caller commits.
    parent : Rocket
        The Rocket reporting the peers (its `id` becomes the new `rocket_id`).
    peers : list[PeerInfo]
        Normalised list of peers extracted from the data source.

    Returns
    -------
    ReconcileResult
        Aggregated outcome — useful for logging and metrics. The returned
        Lr instances are the ones bound to the current session.
    """
    result = ReconcileResult()
    if not peers:
        return result
    for index, peer in enumerate(peers, start=1):
        try:
            await _reconcile_single_peer(session, parent, peer, index, result)
        except Exception:
            # One bad peer must never abort the whole reconciliation pass.
            logger.exception(
                "Discovery: échec sur peer #%d du Rocket '%s' (peer=%r)",
                index, parent.name, peer,
            )
    return result
