"""
Import infrastructure devices from the UISP controller.

Maps each UISP device to our taxonomy and upserts the INFRASTRUCTURE ones
(base-station Rockets, UISP switches, UISP Power, AF60 backhauls) into the
`devices` table — so the operator no longer enters each AP/switch/power/site by
hand. Driven by `uisp_sync_job` on an interval, and triggerable on demand via
POST /api/v1/uisp/sync (with ?dry_run=true to preview without writing).

Scope (deliberately narrow — see CLAUDE.md):
  - Only **name / IP / site(location)** come from UISP. Credentials are stamped
    from the per-family/site conventions (config UISP_*) on CREATE only, and an
    existing device's credentials are NEVER overwritten.
  - **Subscriber stations** (LTU-LR, LiteBeam, …) are IGNORED — CPE
    auto-discovery (discovery_service) owns those rows.
  - A device that **disappears** from UISP is left untouched: no delete, no
    deactivate. The sync only ever creates or updates.

Identity / reconciliation: match an existing device by MAC first (stable across
DHCP churn), then by IP, then by (device_type, name). A match of a different
device_type is treated as a conflict and skipped (never hijack an LR row).
"""

import datetime
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Device, Lr, Rocket
from app.schemas.device import (
    AirFiberCreate,
    PtpLiteBeamCreate,
    RocketCreate,
    UispPowerCreate,
    UispSwitchCreate,
    normalize_mac,
)
from app.services import (
    client_block_service,
    device_service,
    discovery_service,
    uisp_service,
)

logger = logging.getLogger(__name__)

_SAMPLE_CAP = 25  # how many create/update examples to return in the summary


def classify_device(
    uisp_type: str | None, role: str | None, model: str | None,
    wireless_mode: str | None = None,
) -> tuple[str, str | None] | None:
    """Map a UISP (type, role, model, wirelessMode) to (device_type, radio_tech) or None.

    Returns None for everything that is NOT supervised infrastructure
    (subscriber stations, airCube home Wi-Fi, blackBox routers, …). The AF60
    check is FIRST because AF60/AF60-LR report type=airFiber with role=ap *or*
    station — they are point-to-point backhaul infra at both ends.

    PTP LiteBeams (airMAX in point-to-point mode, ``overview.wirelessMode`` =
    ``ap-ptp`` or ``sta-ptp``) are their own infra type at BOTH ends — they are
    neither base-station Rockets (ap-ptmp) nor subscriber LRs (sta-ptmp).
    """
    m = (model or "").upper()
    if m.startswith("AF60"):
        return ("airfiber", None)
    if uisp_type == "uisps" or (uisp_type == "blackBox" and role == "switch"):
        return ("uisp_switch", None)
    if uisp_type == "uispp":
        return ("uisp_power", None)
    # PTP LiteBeam — detected by wireless mode, BEFORE the role=ap rocket mapping
    # (a PTP Main has role=ap but is NOT an AP) and covering the role=station end.
    if uisp_type == "airMax" and (wireless_mode or "").lower() in ("ap-ptp", "sta-ptp"):
        return ("ptp_litebeam", "airmax")
    if role == "ap":
        if uisp_type == "airFiber":   # LTU-Rocket base station
            return ("rocket", "ltu")
        if uisp_type == "airMax":     # Rocket Prism / LiteBeam acting as AP (ptmp)
            return ("rocket", "airmax")
    return None


def site_code(site_name: str | None) -> str | None:
    """Extract the site code used by the credential convention.

    UISP infra sites are named "A2 <CODE>" (e.g. "A2 SNDE" → "SNDE",
    "A2 HQ" → "HQ"). Falls back to the squashed name for anything unexpected.
    """
    if not site_name or not site_name.strip():
        return None
    parts = site_name.strip().split()
    if len(parts) >= 2 and parts[0].upper() == "A2":
        return "".join(parts[1:]).upper()
    return site_name.replace(" ", "").upper()


def _strip_ip(ip_cidr: str | None) -> str | None:
    """UISP returns IPs in CIDR form ("10.135.93.1/16") → bare address."""
    if not ip_cidr:
        return None
    return ip_cidr.split("/")[0].strip() or None


def _build_create_schema(device_type: str, radio_tech: str | None, common: dict, site_name: str | None):
    """Build the right *Create schema with the per-family/site credential convention."""
    settings = get_settings()
    if device_type == "rocket":
        code = site_code(site_name)
        password = (
            settings.uisp_rocket_ssh_password_template.format(site=code) if code else None
        )
        return RocketCreate(
            **common,
            radio_tech=radio_tech or "ltu",
            ssh_username=settings.uisp_rocket_ssh_username or None,
            ssh_password=password,
            ssh_port=443,
        )
    if device_type == "uisp_power":
        return UispPowerCreate(
            **common,
            api_username=settings.uisp_power_api_username or None,
            api_password=settings.uisp_power_api_password or None,
            api_port=443,
        )
    if device_type == "uisp_switch":
        return UispSwitchCreate(**common)  # SNMP-only, community auto-filled
    if device_type == "airfiber":
        return AirFiberCreate(
            **common,
            ssh_username=settings.uisp_af60_ssh_username or None,
            ssh_password=settings.uisp_af60_ssh_password or None,
            ssh_port=443,
        )
    if device_type == "ptp_litebeam":
        # LiteBeams parlent airOS comme les LR airMAX → mêmes creds par défaut.
        return PtpLiteBeamCreate(
            **common,
            ssh_username=settings.lr_default_ssh_username or None,
            ssh_password=settings.lr_default_ssh_password or None,
            ssh_port=443,
        )
    raise ValueError(f"unmapped device_type {device_type!r}")


def _diff_update(
    device: Device, name: str, ip: str | None, location: str | None,
    mac: str | None, by_ip: dict[str, Device],
) -> dict:
    """Compute the name/ip/location/mac changes UISP would apply to an existing row.

    IP is only changed when it is free or already owned by this same device — an
    IP held by another row is reported as `ip_conflict` and left alone (avoids
    fighting the LR DHCP-churn release logic in discovery_service).
    """
    changes: dict = {}
    if name and device.name != name:
        changes["name"] = name
    if ip and device.ip_address != ip:
        owner = by_ip.get(ip)
        if owner is None or owner.id == device.id:
            changes["ip_address"] = ip
        else:
            changes["ip_conflict"] = ip
    if location and device.location != location:
        changes["location"] = location
    # Backfill MAC identity on rows created by hand without one (helps future
    # matching). Never overwrite an existing MAC.
    if mac and not device.mac_address:
        changes["mac_address"] = mac
    return changes


async def _convert_to_ptp_litebeam(session: AsyncSession, dev: Device) -> bool:
    """Reclassify an existing rocket/lr row into a ptp_litebeam (joined-table).

    A device first seen as a base-station Rocket (ap-ptmp wrongly, or before its
    PTP mode was known) or auto-discovered as an LR can turn out to be a PTP
    LiteBeam. Move its subtype row to ptp_litebeams (preserving airOS creds),
    flip the discriminator, and expunge the stale ORM object so the session
    doesn't try to flush the deleted subtype row. Returns False for other types.
    """
    did = dev.id
    src = dev.device_type
    if src == "rocket":
        await session.execute(text(
            "INSERT INTO ptp_litebeams (id, ssh_username, ssh_password, ssh_port, "
            "ssh_host_fingerprint, distance_m) SELECT id, ssh_username, ssh_password, "
            "COALESCE(ssh_port,443), ssh_host_fingerprint, NULL FROM rockets WHERE id=:id"
        ), {"id": did})
        await session.execute(text("DELETE FROM rockets WHERE id=:id"), {"id": did})
    elif src == "lr":
        await session.execute(text(
            "INSERT INTO ptp_litebeams (id, ssh_username, ssh_password, ssh_port, "
            "ssh_host_fingerprint, distance_m) SELECT id, ssh_username, ssh_password, "
            "COALESCE(ssh_port,443), ssh_host_fingerprint, distance_m FROM lrs WHERE id=:id"
        ), {"id": did})
        await session.execute(text("DELETE FROM lrs WHERE id=:id"), {"id": did})
    else:
        return False
    await session.execute(
        text("UPDATE devices SET device_type='ptp_litebeam' WHERE id=:id"), {"id": did}
    )
    session.expunge(dev)
    return True


async def sync_uisp_devices(session: AsyncSession, *, dry_run: bool = False) -> dict:
    """Fetch the UISP inventory and create/update infrastructure devices.

    Returns a summary dict (counts + a capped list of create/update examples).
    The caller is responsible for committing the session (the sync only flushes
    via device_service); in dry_run nothing is written.
    """
    settings = get_settings()
    client = uisp_service.UISPClient(
        settings.uisp_base_url,
        username=settings.uisp_username,
        password=settings.uisp_password,
        api_token=settings.uisp_api_token,
        verify_tls=settings.uisp_verify_tls,
        timeout=settings.uisp_request_timeout,
    )
    raw_devices = await client.fetch_devices()

    existing = (await session.execute(select(Device))).scalars().all()
    by_mac: dict[str, Device] = {d.mac_address: d for d in existing if d.mac_address}
    by_ip: dict[str, Device] = {d.ip_address: d for d in existing if d.ip_address}
    by_type_name: dict[tuple[str, str], Device] = {
        (d.device_type, d.name): d for d in existing
    }

    summary: dict = {
        "dry_run": dry_run,
        "fetched": len(raw_devices),
        "infra_matched": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": {"no_ip": 0, "no_name": 0, "type_conflict": 0, "ip_conflict": 0, "ignored_site": 0},
        "by_type": {},
        "samples": {"create": [], "update": []},
    }

    ignored_sites = settings.uisp_ignored_site_set

    for raw in raw_devices:
        ident = raw.get("identification") or {}
        site_name = (ident.get("site") or {}).get("name")
        site_clean = (site_name or "").strip()

        # No-site device (would land in the "Sans site" bucket) or operator-excluded
        # site (office/LAN gear) → never create or update. "Sans site" n'est pas un
        # vrai site : on n'importe que l'infra rattachée à un site UISP réel.
        if not site_clean or site_clean.lower() in ignored_sites:
            summary["skipped"]["ignored_site"] += 1
            continue

        overview = raw.get("overview") or {}
        wireless_mode = overview.get("wirelessMode")
        # Channel width reported by UISP — mirrored onto the Rocket as a capacity
        # fallback (see Rocket.uisp_channel_width_mhz). Only numeric values count.
        cw_raw = overview.get("channelWidth")
        channel_width = float(cw_raw) if isinstance(cw_raw, (int, float)) else None
        mapping = classify_device(
            ident.get("type"), ident.get("role"), ident.get("model"), wireless_mode,
        )
        if mapping is None:
            continue  # subscriber / out-of-scope — ignored silently (≈1000 rows)
        device_type, radio_tech = mapping
        summary["infra_matched"] += 1

        ip = _strip_ip(raw.get("ipAddress"))
        name = ident.get("name") or ident.get("hostname") or ident.get("displayName")
        mac_raw = ident.get("mac")
        try:
            mac = normalize_mac(mac_raw) if mac_raw else None
        except ValueError:
            mac = None

        if not ip:
            summary["skipped"]["no_ip"] += 1
            continue
        if not name:
            summary["skipped"]["no_name"] += 1
            continue

        # ── Reconcile against an existing row ─────────────────────────────────
        match: Device | None = None
        if mac and mac in by_mac:
            match = by_mac[mac]
        elif ip in by_ip:
            match = by_ip[ip]
        elif (device_type, name) in by_type_name:
            match = by_type_name[(device_type, name)]

        if match is not None:
            if match.device_type != device_type:
                # A rocket/lr that turns out to be a PTP LiteBeam is reclassified
                # in place (not skipped) — this is the recurring case where a PTP
                # station was first stored as an LR client, or a PTP Main as a
                # generic Rocket AP. Any other type mismatch is a real conflict.
                if device_type == "ptp_litebeam" and match.device_type in ("rocket", "lr"):
                    if not dry_run and await _convert_to_ptp_litebeam(session, match):
                        by_mac.pop(match.mac_address, None) if match.mac_address else None
                        logger.info(
                            "UISP sync: '%s' (%s) reclassé %s → ptp_litebeam (lien P2P)",
                            name, ip, match.device_type,
                        )
                    summary["updated"] += 1
                    continue
                summary["skipped"]["type_conflict"] += 1
                logger.warning(
                    "UISP sync: '%s' (%s, %s) matches existing '%s' of type %s "
                    "(expected %s) — skipping to avoid hijack",
                    name, ip, mac or "no-mac", match.name, match.device_type, device_type,
                )
                continue

            changes = _diff_update(match, name, ip, site_name, mac, by_ip)
            if changes.pop("ip_conflict", None):
                summary["skipped"]["ip_conflict"] += 1
            # Mirror the UISP channel width onto the Rocket (capacity fallback) —
            # UISP is the source for this field, so it always tracks the latest.
            if (
                device_type == "rocket" and channel_width is not None
                and match.uisp_channel_width_mhz != channel_width
            ):
                changes["uisp_channel_width_mhz"] = channel_width
            if not changes:
                summary["unchanged"] += 1
                continue
            if not dry_run:
                old_ip = match.ip_address
                for field, value in changes.items():
                    setattr(match, field, value)
                await session.flush()
                # Keep the IP index consistent within this run.
                if "ip_address" in changes:
                    by_ip.pop(old_ip, None)
                    by_ip[changes["ip_address"]] = match
                if "mac_address" in changes:
                    by_mac[changes["mac_address"]] = match
            summary["updated"] += 1
            if len(summary["samples"]["update"]) < _SAMPLE_CAP:
                summary["samples"]["update"].append({
                    "name": name, "ip": ip, "type": device_type, "changes": changes,
                })
            continue

        # ── Create a new infrastructure device ────────────────────────────────
        common = {"name": name, "ip_address": ip, "location": site_name, "mac_address": mac}
        try:
            schema = _build_create_schema(device_type, radio_tech, common, site_name)
        except Exception as exc:  # pydantic validation (e.g. malformed IP)
            summary["skipped"]["no_ip"] += 1
            logger.warning("UISP sync: cannot build %s '%s' (%s): %s", device_type, name, ip, exc)
            continue

        if not dry_run:
            created = await device_service.create_device(session, schema)
            # Stamp the UISP channel width on a freshly-created Rocket (capacity
            # fallback) — not part of the create schema, set directly on the row.
            if device_type == "rocket" and channel_width is not None:
                created.uisp_channel_width_mhz = channel_width
                await session.flush()
            if created.ip_address:
                by_ip[created.ip_address] = created
            if created.mac_address:
                by_mac[created.mac_address] = created
            by_type_name[(device_type, name)] = created

        summary["created"] += 1
        summary["by_type"][device_type] = summary["by_type"].get(device_type, 0) + 1
        if len(summary["samples"]["create"]) < _SAMPLE_CAP:
            summary["samples"]["create"].append({
                "name": name, "ip": ip, "type": device_type,
                "radio_tech": radio_tech, "site": site_name,
            })

    logger.info(
        "UISP sync %s: fetched=%d infra=%d created=%d updated=%d unchanged=%d skipped=%s",
        "(dry-run)" if dry_run else "", summary["fetched"], summary["infra_matched"],
        summary["created"], summary["updated"], summary["unchanged"], summary["skipped"],
    )
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Client stations (CPE / LR) — UISP snapshot into the `lrs` table
# ─────────────────────────────────────────────────────────────────────────────


def _parse_iso(value: str | None) -> datetime.datetime | None:
    """Parse a UISP ISO timestamp ("...Z") to an aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _build_station_ap_map(links: list[dict]) -> dict[str, str]:
    """Map each station **device id → its AP device name**, from provisioned
    data-links.

    For every AP↔station data-link we take the ``role=="ap"`` end as the AP and
    the ``role=="station"`` end as the client. This is the roster UISP's own UI
    counts against — it attributes a client to its AP even when the station's
    ``apDevice`` attribute is empty (UISP leaves it null for some active
    stations, which made our ``apDevice``-only count under-report). When a
    station has several links (roaming history), an ``active`` link wins over a
    ``disconnected`` one so the current AP is used. AP↔switch (wired uplink) and
    AP↔AP (PTP backhaul) links are naturally ignored (no station end)."""
    chosen: dict[str, tuple[bool, str]] = {}  # station_id -> (link_active, ap_name)
    for link in links:
        ends = [
            ((link.get(side) or {}).get("device") or {}).get("identification") or {}
            for side in ("from", "to")
        ]
        ap = next((e for e in ends if e.get("role") == "ap"), None)
        sta = next((e for e in ends if e.get("role") == "station"), None)
        if not ap or not sta:
            continue
        sid, ap_name = sta.get("id"), ap.get("name")
        if not sid or not ap_name:
            continue
        active = link.get("state") == "active"
        prev = chosen.get(sid)
        if prev is None or (active and not prev[0]):
            chosen[sid] = (active, ap_name)
    return {sid: name for sid, (_, name) in chosen.items()}


def _norm_name(value: str | None) -> str:
    """Clé de rapprochement d'un nom d'AP (UISP ↔ notre inventaire).

    Les noms UISP arrivent avec des espaces parasites et une casse variable
    (` A2-HQ-SUD ` a réellement été vu en base) : un rapprochement strict
    laisserait le client orphelin sans que rien ne le signale.
    """
    return (value or "").strip().casefold()


async def _adopt_uisp_attribution(
    session: AsyncSession,
    lr: Lr,
    ap_name: str | None,
    ip: str | None,
    uisp_last_seen: datetime.datetime | None,
    rockets_by_norm_name: dict[str, Rocket],
    summary: dict,
) -> None:
    """Reprend l'AP et l'IP que UISP connaît — SEULEMENT s'il a vu plus récemment.

    Pourquoi c'est nécessaire : le rattachement radio (`discovery_service`) ne
    peut agir que sur un client **allumé**, puisqu'il lit la liste des stations
    de l'AP. Un client qui déménage puis tombe en panne n'est donc corrigé par
    personne : sa ligne reste figée sur son ANCIEN AP, son ancien site et son
    ancienne IP — morte, donc plus pingeable, donc « hors ligne » pour toujours.
    Constaté le 2026-07-22 : un abonné servi par A2-DN1-SUD1 depuis 3 semaines,
    affiché sur A2 AT1 avec une IP périmée, alors que la colonne `uisp_ap_name`
    de SA PROPRE LIGNE portait déjà le bon AP.

    Règle de priorité — **la source qui l'a vu le plus récemment gagne** :
    tant que le radio le voit (poll toutes les 60 s), il fait foi et UISP ne
    touche à rien ; dès que le radio le perd, l'instantané UISP prend le relais.
    Sans cet arbitrage, deux écrivains sur `rocket_id` le feraient osciller à
    chaque cycle.
    """
    if uisp_last_seen is None:
        return
    if uisp_last_seen.tzinfo is None:
        uisp_last_seen = uisp_last_seen.replace(tzinfo=datetime.UTC)
    seen_by_radio = lr.last_discovered_at
    if seen_by_radio is not None:
        if seen_by_radio.tzinfo is None:
            seen_by_radio = seen_by_radio.replace(tzinfo=datetime.UTC)
        if seen_by_radio >= uisp_last_seen:
            return  # le radio l'a vu plus récemment : il reste propriétaire

    parent = rockets_by_norm_name.get(_norm_name(ap_name)) if ap_name else None
    if parent is not None and lr.rocket_id != parent.id:
        logger.info(
            "UISP: LR '%s' rerattaché %s → '%s' (UISP l'a vu plus récemment que le radio)",
            lr.name, lr.rocket_id, parent.name,
        )
        lr.rocket_id = parent.id
        # Le site suit l'AP : un CPE est physiquement chez son Rocket parent.
        lr.location = parent.location
        summary["reparented"] += 1

    # L'IP passe par le MÊME garde-fou que la découverte : hors du plan de
    # management, elle est écartée (UISP remonte aussi des LAN de CPE), et la
    # libération de l'ancien détenteur suit la règle unique de discovery_service
    # — deux écrivains divergents sur une contrainte UNIQUE, c'est le vol d'IP.
    if (
        ip
        and ip != lr.ip_address
        and discovery_service.is_management_ip(ip)
        and await discovery_service.release_ip_if_held(session, ip, exclude_id=lr.id)
    ):
        logger.info(
            "UISP: LR '%s' — IP reprise %s → %s (source UISP, radio muet)",
            lr.name, lr.ip_address, ip,
        )
        lr.ip_address = ip
        summary["ip_updated"] += 1


async def sync_uisp_stations(session: AsyncSession, *, dry_run: bool = False) -> dict:
    """Import the UISP client-station roster into the `lrs` table.

    Unlike `sync_uisp_devices` (infrastructure), this brings in subscriber LRs so
    /access can show every client — and its bridge/router mode — even when our
    own live poll has nothing (Rocket down, LR never discovered). It writes the
    `uisp_*` snapshot columns (mode/status/last_seen/ap_name) plus name/IP on
    create, and NEVER touches the block state or `topology_mode` — those stay
    live-owned.

    ⚠️ `rocket_id`/`location`/`ip_address` of an EXISTING row are shared with
    discovery_service, under one arbitration rule: **the source that saw the
    station most recently wins** (`_adopt_uisp_attribution`). Radio discovery
    only sees a powered-on client, so a client that moves then goes down was
    corrected by nobody and stayed pinned to its former AP, site and (dead) IP
    forever — while its own `uisp_ap_name` already held the right answer.

    Reconciliation is MAC-first (same identity discovery uses) so a station that
    is later discovered over the radio converges onto the same row. AF60 backhaul
    stations are excluded (already infra); everything else UISP lists is imported
    (the full roster — UISP already drops de-provisioned stations).

    Returns a summary dict; the caller commits (nothing is written in dry_run).
    """
    settings = get_settings()
    client = uisp_service.UISPClient(
        settings.uisp_base_url,
        username=settings.uisp_username,
        password=settings.uisp_password,
        api_token=settings.uisp_api_token,
        verify_tls=settings.uisp_verify_tls,
        timeout=settings.uisp_request_timeout,
    )
    raw = await client.fetch_devices(role="station")
    # Attribute each station to its AP via the provisioned data-links (reliable,
    # up or down) rather than the station's `apDevice` attribute alone — UISP
    # leaves that attribute null for some active stations, which under-reported
    # the installed roster. Best-effort: on failure we fall back to `apDevice`.
    try:
        ap_by_station = _build_station_ap_map(await client.fetch_data_links())
    except Exception as exc:  # noqa: BLE001 — non-fatal, apDevice fallback remains
        logger.warning("UISP data-links fetch failed (%s) — using apDevice attribution only", exc)
        ap_by_station = {}

    # Every MAC UISP currently lists as a station — used after the loop to clear
    # the AP attribution of rows UISP no longer knows (deprovisioned clients).
    roster_macs: set[str] = set()
    for dev in raw:
        m = (dev.get("identification") or {}).get("mac")
        try:
            nm = normalize_mac(m) if m else None
        except ValueError:
            nm = None
        if nm:
            roster_macs.add(nm)

    existing = (await session.execute(select(Device))).scalars().all()
    by_mac: dict[str, Device] = {d.mac_address: d for d in existing if d.mac_address}
    by_ip: dict[str, Device] = {d.ip_address: d for d in existing if d.ip_address}
    rockets_by_name: dict[str, Rocket] = {
        d.name: d for d in existing if isinstance(d, Rocket)
    }
    # Rapprochement tolérant (espaces/casse) — les noms UISP ne sont pas propres.
    rockets_by_norm_name: dict[str, Rocket] = {
        _norm_name(d.name): d for d in existing if isinstance(d, Rocket)
    }

    now = datetime.datetime.now(datetime.UTC)

    summary: dict = {
        "dry_run": dry_run,
        "fetched": len(raw),
        "stations": 0,
        "created": 0,
        "updated": 0,
        # Corrections reprises de UISP quand le radio ne voit plus la station
        # (cf. `_adopt_uisp_attribution`) — c'est CE résumé qui les porte, pas
        # celui du sync infra, qui ne réconcilie aucun client.
        "reparented": 0,
        "ip_updated": 0,
        "skipped": {
            "af60": 0, "no_mac": 0, "type_conflict": 0,
        },
        "samples": {"create": [], "update": []},
    }

    for dev in raw:
        ident = dev.get("identification") or {}
        model = ident.get("model") or ""
        # AF60 backhaul reports role=station at one end — it is infra, owned by
        # sync_uisp_devices, never a client LR.
        if model.upper().startswith("AF60"):
            summary["skipped"]["af60"] += 1
            continue
        # PTP LiteBeam station end (sta-ptp) is infra (ptp_litebeam), owned by
        # sync_uisp_devices — NOT a client LR. Skip so it's never imported here.
        if ((dev.get("overview") or {}).get("wirelessMode") or "").lower() in ("ap-ptp", "sta-ptp"):
            summary["skipped"]["ptp"] = summary["skipped"].get("ptp", 0) + 1
            continue

        last_seen = _parse_iso((dev.get("overview") or {}).get("lastSeen"))

        mac_raw = ident.get("mac")
        try:
            mac = normalize_mac(mac_raw) if mac_raw else None
        except ValueError:
            mac = None
        if not mac:
            summary["skipped"]["no_mac"] += 1  # MAC is the identity — can't import
            continue

        summary["stations"] += 1

        name = ident.get("name") or ident.get("hostname") or ident.get("displayName")
        ip = _strip_ip(dev.get("ipAddress"))
        uisp_mode = dev.get("mode")  # 'router' | 'bridge'
        uisp_status = (dev.get("overview") or {}).get("status")  # 'active'|'disconnected'
        # Prefer the data-link attribution (covers stations whose apDevice is
        # null); fall back to the station's own apDevice attribute.
        ap_name = ap_by_station.get(ident.get("id")) or (
            ((dev.get("attributes") or {}).get("apDevice") or {}).get("name")
        )
        model_name = ident.get("modelName") or model

        match = by_mac.get(mac)
        if match is not None:
            if match.device_type != "lr":
                summary["skipped"]["type_conflict"] += 1
                continue
            # Update the UISP snapshot columns + the (CRM) name. Never touch the
            # live-owned columns or a held IP.
            if not dry_run:
                if name and match.name != name:
                    match.name = name
                match.uisp_mode = uisp_mode
                match.uisp_status = uisp_status
                match.uisp_last_seen = last_seen
                match.uisp_ap_name = ap_name
                match.uisp_synced_at = now
                await _adopt_uisp_attribution(
                    session, match, ap_name, ip, last_seen,
                    rockets_by_norm_name, summary,
                )
            summary["updated"] += 1
            if len(summary["samples"]["update"]) < _SAMPLE_CAP:
                summary["samples"]["update"].append(
                    {"name": name, "mac": mac, "mode": uisp_mode, "status": uisp_status},
                )
            continue

        # ── Create a new LR row from the UISP roster ─────────────────────────
        parent = rockets_by_name.get(ap_name) if ap_name else None
        model_variant = discovery_service._infer_model_variant({"model": model_name}, parent)
        # The IP is taken only if free; an IP held by another device is left to
        # discovery_service's churn logic (this row keeps NULL until rediscovered).
        new_ip = ip if ip and ip not in by_ip else None

        if not dry_run:
            lr = Lr(
                name=name or f"LR {mac}",
                ip_address=new_ip,
                status="unknown",
                location=parent.location if parent else (ap_name or None),
                mac_address=mac,
                model_variant=model_variant,
                rocket_id=parent.id if parent else None,
                auto_discovered=True,
                ssh_username=settings.lr_default_ssh_username or None,
                ssh_password=settings.lr_default_ssh_password or None,
                ssh_port=settings.lr_default_ssh_port,
                lan_interface=client_block_service.default_lan_interface(model_variant),
                uisp_mode=uisp_mode,
                uisp_status=uisp_status,
                uisp_last_seen=last_seen,
                uisp_ap_name=ap_name,
                uisp_synced_at=now,
            )
            session.add(lr)
            await session.flush()
            by_mac[mac] = lr
            if new_ip:
                by_ip[new_ip] = lr

        summary["created"] += 1
        if len(summary["samples"]["create"]) < _SAMPLE_CAP:
            summary["samples"]["create"].append({
                "name": name, "mac": mac, "ip": new_ip, "variant": model_variant,
                "mode": uisp_mode, "status": uisp_status, "ap": ap_name,
            })

    # Reconcile the roster: an LR that carries a UISP AP attribution but whose
    # MAC is no longer in the fetched station list has been deprovisioned in
    # UISP. Since the sync never deletes rows, without this its stale
    # `uisp_ap_name` would keep it counted in /capacity forever (the "25 vs 24"
    # phantom). Clear only the UISP snapshot columns — discovery-owned columns
    # (rocket_id, topology_mode, block state) stay untouched.
    summary["cleared_ap"] = 0
    if not dry_run:
        for d in existing:
            if isinstance(d, Lr) and d.uisp_ap_name and d.mac_address not in roster_macs:
                d.uisp_ap_name = None
                d.uisp_status = None
                d.uisp_synced_at = now
                summary["cleared_ap"] += 1

    logger.info(
        "UISP station sync %s: fetched=%d stations=%d created=%d updated=%d "
        "reparented=%d ip_updated=%d cleared_ap=%d skipped=%s",
        "(dry-run)" if dry_run else "", summary["fetched"], summary["stations"],
        summary["created"], summary["updated"], summary["reparented"],
        summary["ip_updated"], summary["cleared_ap"], summary["skipped"],
    )
    return summary
