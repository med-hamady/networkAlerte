"""
Warning digest service — batches groupable warnings into a single notification.

Workflow (run periodically by warning_digest_job in tasks/jobs.py):
  1. Query open incidents with alert_type whose policy is groupable=True
     and digested_at is NULL (i.e. not yet sent in any digest).
  2. Resolve channels exactly like notification_service does (DB-first, env fallback).
  3. Apply the policy gate per channel (using the *base* alert_type policy —
     overrides per-device are applied to filter out any incident whose device
     opts out of digest channels entirely).
  4. Send a single payload per channel via alert_formatter.format_digest_*.
  5. Mark every included incident as digested_at = now.

Critical incidents are never digested — they go through the immediate path
in notification_service. Recovery messages are also not digested.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import AlertChannel, Severity
from app.models.device import Device
from app.models.incident import Incident
from app.services import (
    alert_formatter,
    email_service,
    notification_service,
    whatsapp_service,
)
from app.services.alert_policy import get_policy, get_policy_for_device

logger = logging.getLogger(__name__)


async def _collect_undigested_warnings(
    db: AsyncSession,
) -> list[tuple[Device, Incident]]:
    """
    Pull open warning incidents that are eligible for digest and not yet sent.

    Joins device by device_id so the formatter can render the message without
    triggering a lazy load.
    """
    result = await db.execute(
        select(Incident, Device)
        .join(Device, Device.id == Incident.device_id)
        .where(
            Incident.status == "open",
            Incident.digested_at.is_(None),
            Incident.severity == Severity.WARNING,
        )
        .order_by(Incident.detected_at.asc())
    )
    items: list[tuple[Device, Incident]] = []
    for incident, device in result.all():
        base = get_policy(incident.alert_type)
        if not base.groupable:
            continue
        # Per-device override might disable groupable for this device — respect it
        effective = get_policy_for_device(
            incident.alert_type, getattr(device, "policy_overrides", None),
        )
        if not effective.groupable:
            continue
        items.append((device, incident))
    return items


def _channels_for_digest(
    targets: list,
    items: list[tuple[Device, Incident]],
) -> list:
    """
    Filter resolved channel targets to those allowed by *at least one*
    item's policy. If no item allows a given channel, the channel is skipped.
    """
    if not items:
        return []

    allowed_channels: set[str] = set()
    for device, incident in items:
        policy = get_policy_for_device(
            incident.alert_type, getattr(device, "policy_overrides", None),
        )
        for channel in policy.channels:
            allowed_channels.add(channel)

    return [t for t in targets if t.kind in allowed_channels]


async def _deliver_digest(target, items: list[tuple[Device, Incident]]) -> bool:
    """Send a single digest payload through one channel target."""
    if target.kind == AlertChannel.WHATSAPP:
        text = alert_formatter.format_digest_for_whatsapp(items)
        return await whatsapp_service.send_whatsapp(text)
    if target.kind == AlertChannel.EMAIL:
        subject, text_body, html_body = alert_formatter.format_digest_for_email(items)
        return await email_service.send_email(
            target.recipients, subject, text_body, html_body,
        )
    logger.warning("Unknown channel kind for digest: %s", target.kind)
    return False


async def flush_warning_digest(db: AsyncSession) -> int:
    """
    Send the pending warning digest and mark items as digested.

    Returns the number of incidents included in the digest.
    """
    items = await _collect_undigested_warnings(db)
    if not items:
        logger.debug("Digest: no undigested warnings — skipping")
        return 0

    targets = await notification_service._resolve_channels()
    targets = _channels_for_digest(targets, items)
    now = datetime.datetime.now(datetime.UTC)

    if not targets:
        logger.error(
            "Digest: %d warnings pending but no channel matches policy", len(items),
        )
        return 0

    delivered = False
    for target in targets:
        ok = await _deliver_digest(target, items)
        delivered = delivered or ok
        logger.info(
            "Digest: sent %d warnings via %s (%s) → %s",
            len(items), target.kind, target.label, "ok" if ok else "failed",
        )

    if delivered:
        for _, incident in items:
            incident.digested_at = now
    else:
        # All channels failed — leave digested_at NULL so we retry next cycle.
        logger.error(
            "Digest: %d warnings — all %d channel(s) failed to deliver",
            len(items), len(targets),
        )

    return len(items)
