"""
Notification service — dispatch alerts via WhatsApp (Ultramsg).

Channel resolution:
  WhatsApp REPLACES email as the transport. The channel is resolved from the
  env-based config (WHATSAPP_ENABLED + WHATSAPP_INSTANCE_ID + WHATSAPP_TOKEN +
  WHATSAPP_GROUP_ID); messages land in the configured WhatsApp group.

Each candidate channel is then gated by the alert_policy: it only fires
if `whatsapp` appears in policy.channels for the alert_type and the event
(opened/resolved) is allowed by the policy.

Failure on one channel does not block the others. Returns True if at
least one channel delivered successfully.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.core.alert_constants import AlertChannel, NotificationEvent, Severity
from app.core.config import get_settings
from app.models.device import Device
from app.models.incident import Incident
from app.services import (
    alert_formatter,
    email_service,
    whatsapp_service,
)
from app.services.alert_policy import get_policy_for_device, should_notify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delivery safety net (incident 2026-05-17)
#
# A single channel must never be able to stall a caller. The reconcile/alert
# path opens many incidents in one loop; with a dead SMTP server every send
# blocked ~60-90s on the connect timeout, serialising discovery to a crawl and
# delaying the job's commit (LR re-attachment never persisted in time).
#
# Two guards, both here so EVERY caller (discovery, alert_engine, jobs) is
# covered without touching their transaction structure:
#   1. Hard per-delivery timeout — a hung channel is capped, never unbounded.
#   2. Per-channel cooldown — once a channel times out OR fails repeatedly,
#      skip it for a short window instead of eating the cost again on every
#      subsequent incident of the same burst. Auto-recovers after the cooldown.
#      A hang (timeout/exception) trips the cooldown at once; a fast reject
#      (e.g. a throttling SMTP returning quickly) trips it after N in a row.
# ---------------------------------------------------------------------------

_DELIVERY_TIMEOUT_S = 8.0
_CHANNEL_COOLDOWN_S = 120.0
# Consecutive failed deliveries (incl. fast False returns) before cooldown.
_MAX_CONSECUTIVE_FAILURES = 5

# channel key -> monotonic time until which the channel is considered degraded
_degraded_until: dict[str, float] = {}
# channel key -> count of consecutive failed deliveries (reset on success)
_consecutive_fail: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Internal channel descriptor
# ---------------------------------------------------------------------------

class _ChannelTarget:
    """Single delivery target resolved from the env config."""

    __slots__ = ("kind", "label", "recipients")

    def __init__(self, kind: str, label: str, recipients: list[str]) -> None:
        self.kind = kind          # AlertChannel.WHATSAPP today
        self.label = label        # for logging
        self.recipients = recipients  # unused for WhatsApp (single group target)


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------

def _channels_from_env() -> list[_ChannelTarget]:
    """Build the env-based channel list — WhatsApp (Ultramsg group)."""
    settings = get_settings()
    targets: list[_ChannelTarget] = []
    if settings.whatsapp_configured:
        targets.append(_ChannelTarget(
            AlertChannel.WHATSAPP, "env:Ultramsg", recipients=[],
        ))
    return targets


async def _resolve_channels() -> list[_ChannelTarget]:
    """Resolve the active notification channels from the env-based config."""
    return _channels_from_env()


# ---------------------------------------------------------------------------
# Per-target delivery
# ---------------------------------------------------------------------------

async def _deliver(
    target: _ChannelTarget,
    device: Device,
    incident: Incident,
    event: str,
) -> bool:
    """Send the alert through a single resolved channel target."""
    if target.kind == AlertChannel.WHATSAPP:
        text = alert_formatter.format_for_whatsapp(device, incident, event)
        return await whatsapp_service.send_whatsapp(text)
    if target.kind == AlertChannel.EMAIL:
        subject, text_body, html_body = alert_formatter.format_for_email(
            device, incident, event,
        )
        return await email_service.send_email(target.recipients, subject, text_body, html_body)
    logger.warning("Unknown channel kind %r — skipping", target.kind)
    return False


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def _dispatch(device: Device, incident: Incident, event: str) -> bool:
    """Resolve channels, gate by policy, deliver — return True if any succeeded."""
    policy = get_policy_for_device(incident.alert_type, getattr(device, "policy_overrides", None))

    # Groupable warnings are batched by the digest job, not sent immediately.
    # Critical incidents and recovery events still go through.
    if (
        event == NotificationEvent.OPENED
        and policy.groupable
        and incident.severity != Severity.CRITICAL
    ):
        logger.debug(
            "Deferring %s/%s to warning digest (groupable=True)",
            incident.alert_type, event,
        )
        return False

    targets = await _resolve_channels()

    if not targets:
        logger.debug("No notification channels configured (DB or env)")
        return False

    results: list[bool] = []
    now = time.monotonic()
    for target in targets:
        if not should_notify(target.kind, policy, incident.severity, event):
            logger.debug(
                "Skipping %s (%s) — policy excludes channel for %s/%s",
                target.kind, target.label, incident.alert_type, event,
            )
            continue

        key = f"{target.kind}:{target.label}"
        degraded_until = _degraded_until.get(key, 0.0)
        if now < degraded_until:
            # Channel recently timed out — skip fast so a dead channel can't
            # serialise the caller's loop. Treated as a failed delivery.
            logger.debug(
                "Skipping degraded channel %s (%s) — cooldown %.0fs left",
                target.kind, target.label, degraded_until - now,
            )
            results.append(False)
            continue

        try:
            ok = await asyncio.wait_for(
                _deliver(target, device, incident, event),
                timeout=_DELIVERY_TIMEOUT_S,
            )
        except TimeoutError:
            _degraded_until[key] = time.monotonic() + _CHANNEL_COOLDOWN_S
            logger.error(
                "Notification channel %s (%s) timed out after %.0fs — "
                "marking degraded for %.0fs (alert %s/%s)",
                target.kind, target.label, _DELIVERY_TIMEOUT_S,
                _CHANNEL_COOLDOWN_S, incident.alert_type, event,
            )
            ok = False
        except Exception:
            _degraded_until[key] = time.monotonic() + _CHANNEL_COOLDOWN_S
            logger.exception(
                "Notification channel %s (%s) raised — marking degraded for "
                "%.0fs (alert %s/%s)",
                target.kind, target.label, _CHANNEL_COOLDOWN_S,
                incident.alert_type, event,
            )
            ok = False

        # Track consecutive failures so a fast-rejecting channel (e.g. a
        # throttling SMTP that returns False quickly without raising) also
        # gets backed off — the timeout/exception paths above only catch
        # hangs. Any success clears the streak.
        if ok:
            _consecutive_fail.pop(key, None)
        else:
            streak = _consecutive_fail.get(key, 0) + 1
            _consecutive_fail[key] = streak
            # Reaching here means we are NOT in an active cooldown (the skip
            # block above `continue`s otherwise), so re-arming is always safe.
            if streak >= _MAX_CONSECUTIVE_FAILURES:
                _degraded_until[key] = time.monotonic() + _CHANNEL_COOLDOWN_S
                _consecutive_fail.pop(key, None)
                logger.error(
                    "Notification channel %s (%s) failed %d× in a row — "
                    "marking degraded for %.0fs (alert %s/%s)",
                    target.kind, target.label, streak, _CHANNEL_COOLDOWN_S,
                    incident.alert_type, event,
                )

        results.append(ok)

    if not results:
        logger.debug(
            "No channel matched policy for %s/%s", incident.alert_type, event,
        )
        return False
    return any(results)


async def notify_incident_opened(device: Device, incident: Incident) -> bool:
    """Notify configured channels when a new incident is opened, gated by policy."""
    return await _dispatch(device, incident, NotificationEvent.OPENED)


async def notify_incident_resolved(device: Device, incident: Incident) -> bool:
    """Notify configured channels when an incident is resolved, gated by policy."""
    return await _dispatch(device, incident, NotificationEvent.RESOLVED)


async def notify_security_event(
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    """Send a security/audit alert via the configured WhatsApp channel.

    Bypasses the Incident pipeline — security events are system-level (no
    device id) and must not depend on alert_policy gating. The per-delivery
    timeout matches the incident path so a stalled channel cannot freeze the
    detection job. `body_html` is accepted for backward compatibility but
    ignored (WhatsApp is plain text). Returns True if delivered.
    """
    targets = await _resolve_channels()
    if not targets:
        logger.warning(
            "notify_security_event: no notification channels configured — "
            "security alert NOT sent (subject=%r)",
            subject,
        )
        return False
    # WhatsApp has no subject — fold it into the first (bold) line of the body.
    message = f"*{subject}*\n{body_text}" if subject else body_text
    delivered = False
    for target in targets:
        if target.kind != AlertChannel.WHATSAPP:
            continue
        try:
            ok = await asyncio.wait_for(
                whatsapp_service.send_whatsapp(message),
                timeout=_DELIVERY_TIMEOUT_S,
            )
        except TimeoutError:
            logger.error(
                "notify_security_event: channel %s timed out after %.0fs",
                target.label, _DELIVERY_TIMEOUT_S,
            )
            ok = False
        except Exception:
            logger.exception(
                "notify_security_event: channel %s raised", target.label,
            )
            ok = False
        delivered = delivered or bool(ok)
    return delivered
