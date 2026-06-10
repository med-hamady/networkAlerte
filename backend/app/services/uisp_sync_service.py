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

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Device
from app.schemas.device import (
    AirFiberCreate,
    RocketCreate,
    UispPowerCreate,
    UispSwitchCreate,
    normalize_mac,
)
from app.services import device_service, uisp_service

logger = logging.getLogger(__name__)

_SAMPLE_CAP = 25  # how many create/update examples to return in the summary


def classify_device(
    uisp_type: str | None, role: str | None, model: str | None,
) -> tuple[str, str | None] | None:
    """Map a UISP (type, role, model) to (device_type, radio_tech) or None.

    Returns None for everything that is NOT supervised infrastructure
    (subscriber stations, airCube home Wi-Fi, blackBox routers, …). The AF60
    check is FIRST because AF60/AF60-LR report type=airFiber with role=ap *or*
    station — they are point-to-point backhaul infra at both ends.
    """
    m = (model or "").upper()
    if m.startswith("AF60"):
        return ("airfiber", None)
    if uisp_type == "uisps" or (uisp_type == "blackBox" and role == "switch"):
        return ("uisp_switch", None)
    if uisp_type == "uispp":
        return ("uisp_power", None)
    if role == "ap":
        if uisp_type == "airFiber":   # LTU-Rocket base station
            return ("rocket", "ltu")
        if uisp_type == "airMax":     # Rocket Prism / LiteBeam acting as AP
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
        "skipped": {"no_ip": 0, "no_name": 0, "type_conflict": 0, "ip_conflict": 0},
        "by_type": {},
        "samples": {"create": [], "update": []},
    }

    for raw in raw_devices:
        ident = raw.get("identification") or {}
        mapping = classify_device(ident.get("type"), ident.get("role"), ident.get("model"))
        if mapping is None:
            continue  # subscriber / out-of-scope — ignored silently (≈1000 rows)
        device_type, radio_tech = mapping
        summary["infra_matched"] += 1

        ip = _strip_ip(raw.get("ipAddress"))
        name = ident.get("name") or ident.get("hostname") or ident.get("displayName")
        site_name = (ident.get("site") or {}).get("name")
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
