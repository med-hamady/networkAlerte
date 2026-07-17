"""Per-client subscription plan (forfait) — read from the LR's airOS shaper.

The customer's plan is not exposed by any device HTTP API (LTU /statistics and
airOS status.cgi carry only live radio/throughput). It is provisioned on the LR
itself as an airOS *traffic shaper* — an egress rate cap per interface stored in
/tmp/system.cfg. On a CPE the wired interface faces the customer (egress =
download) and the radio faces the AP (egress = upload), so the two caps are the
plan's down/up Mbps. We read them over the same SSH channel the client-block
feature already uses.

Caveat: the LR knows the *speeds*, not the commercial plan *name* — that lives
only in the UISP CRM. If a label is needed, map (download, upload) → name on our
side, or join to the CRM separately.

This sync ALSO caches the LR's provisioned GPS coordinates
(`system.latitude`/`system.longitude`), because they sit in the very same
/tmp/system.cfg: one grep on the session we already open, instead of a second
SSH round-trip per LR for two lines. See `ssh_service.parse_system_location` —
it is provisioned data, NOT a GPS fix, and all three firmware families (LTU,
airMAX AC, M5) carry it when the installer filled it in.
"""

import asyncio
import datetime
import functools
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.services import ssh_service

logger = logging.getLogger(__name__)


def _has_ssh(lr: Lr) -> bool:
    return bool(lr.ssh_username and lr.ssh_password)


async def get_lr_plan(lr: Lr) -> tuple[bool, dict | None, str]:
    """Read the traffic-shaper rate caps (forfait) from an LR over SSH.

    Returns ``(ok, plan|None, message)``. ``plan`` is the dict produced by
    :func:`ssh_service.parse_tshaper_config` (shaper_enabled, download_mbps,
    upload_mbps, rules). Pins the host key on first sight and promotes a working
    fallback SSH password onto the LR row (auto-heal), exactly like the
    client-block path — the caller's session commit persists both.
    """
    if not _has_ssh(lr):
        return (
            False, None,
            f"Le LR {lr.name} n'a pas d'identifiants SSH — impossible de lire "
            f"le forfait. Configure ssh_username/ssh_password via "
            f"PUT /api/v1/devices/{lr.id}.",
        )

    settings = get_settings()
    primary_pw = lr.ssh_password
    ok, plan, msg, observed_fp, used_pw = await ssh_service.read_traffic_shaper(
        host=lr.ip_address,
        port=lr.ssh_port or 22,
        username=lr.ssh_username,
        password=primary_pw,
        expected_fingerprint=lr.ssh_host_fingerprint,
        fallback_passwords=settings.lr_fallback_password_list,
    )
    if ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
        lr.ssh_host_fingerprint = observed_fp
    if used_pw and primary_pw and used_pw != primary_pw:
        logger.info(
            "lr_plan: LR '%s' (%s) — fallback SSH password succeeded, promoting "
            "on LR row.",
            lr.name, lr.ip_address,
        )
        lr.ssh_password = used_pw
    return ok, plan, msg


async def sync_all_lr_plans(session: AsyncSession) -> dict:
    """Read every reachable LR's traffic-shaper plan over SSH and cache the caps.

    Mirrors ``lr_internet_probe_job``'s two-phase shape: snapshot the targets,
    fan the (sync paramiko) reads out on a thread pool bounded by
    ``lr_probe_concurrency``, then persist serially. Only LRs that are ``up``,
    have SSH credentials and an IP are probed — a down LR would just burn an SSH
    timeout. Each LR's ``plan_download_mbps`` / ``plan_upload_mbps`` /
    ``plan_synced_at`` are updated; a read that finds no shaper clears the caps
    to None (the forfait was removed on the device). Returns a summary dict::

        {"eligible": N, "updated": N, "no_shaper": N, "failed": N}
    """
    settings = get_settings()
    result = await session.execute(
        select(Lr).where(
            Lr.ssh_username.is_not(None),
            Lr.ssh_password.is_not(None),
            Lr.status == "up",
            Lr.ip_address.is_not(None),
        )
    )
    # Snapshot the fields the SSH read needs while the session row is loaded —
    # Phase 1 runs outside the session.
    targets = [
        (lr.id, lr.name, lr.ip_address, lr.ssh_port or 22,
         lr.ssh_username, lr.ssh_password, lr.ssh_host_fingerprint)
        for lr in result.scalars().all()
    ]

    summary = {
        "eligible": len(targets), "updated": 0, "no_shaper": 0, "failed": 0,
        "location_updated": 0, "no_location": 0,
    }
    if not targets:
        logger.debug("lr_plan sync: aucun LR up avec credentials SSH — ignoré")
        return summary

    logger.info("lr_plan sync — lecture du forfait sur %d LR(s)", len(targets))

    # ── Phase 1 : lire le shaper de tous les LR EN PARALLÈLE (borné) ──
    loop = asyncio.get_running_loop()
    results: dict[int, tuple] = {}

    async def _read(t: tuple, pool: ThreadPoolExecutor) -> None:
        dev_id, name, ip, port, user, pwd, fp = t
        try:
            results[dev_id] = await loop.run_in_executor(
                pool,
                functools.partial(
                    ssh_service._read_traffic_shaper_sync,
                    ip, port, user, pwd, fp, settings.lr_fallback_password_list,
                ),
            )
        except Exception:
            logger.exception("lr_plan sync: lecture %s (%s) a crashé", name, ip)

    with ThreadPoolExecutor(
        max_workers=settings.lr_probe_concurrency, thread_name_prefix="lr-plan",
    ) as pool:
        await asyncio.gather(
            *[_read(t, pool) for t in targets], return_exceptions=True,
        )

    # ── Phase 2 : persistance DB séquentielle ──
    now = datetime.datetime.now(datetime.UTC)
    for dev_id, (ok, plan, _msg, observed_fp, used_pw) in results.items():
        dev = await session.get(Lr, dev_id)
        if dev is None:
            continue
        if observed_fp and dev.ssh_host_fingerprint != observed_fp:
            dev.ssh_host_fingerprint = observed_fp
        if used_pw and used_pw != dev.ssh_password:
            logger.info(
                "lr_plan sync: LR '%s' (%s) — fallback password succeeded → "
                "promoting on LR.",
                dev.name, dev.ip_address,
            )
            dev.ssh_password = used_pw
        if not ok:
            summary["failed"] += 1
            await session.commit()
            continue
        dl = plan.get("download_mbps") if plan else None
        ul = plan.get("upload_mbps") if plan else None
        dev.plan_download_mbps = dl
        dev.plan_upload_mbps = ul
        dev.plan_synced_at = now
        # Coordinates ride on the same system.cfg read. Every family carries the
        # key when provisioned; a None just means this unit has none (absent, or
        # present-but-empty as on the M5), so never clear a known position on a
        # None — an unprovisioned read must not wipe a value some other pass, or
        # a human, put there.
        lat = plan.get("latitude") if plan else None
        lon = plan.get("longitude") if plan else None
        if lat is not None and lon is not None:
            if dev.latitude != lat or dev.longitude != lon:
                summary["location_updated"] += 1
            dev.latitude = lat
            dev.longitude = lon
        else:
            summary["no_location"] += 1
        if dl is None and ul is None:
            summary["no_shaper"] += 1
        else:
            summary["updated"] += 1
        await session.commit()

    logger.info(
        "lr_plan sync terminé — eligible=%d updated=%d no_shaper=%d failed=%d "
        "location_updated=%d no_location=%d",
        summary["eligible"], summary["updated"], summary["no_shaper"],
        summary["failed"], summary["location_updated"], summary["no_location"],
    )
    return summary
