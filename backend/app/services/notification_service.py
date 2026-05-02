"""
Notification service — dispatch alerts via the configured channels.

Channel resolution:
  1. Read enabled rows from the notification_channels table (DB-first).
  2. If at least one enabled row exists, use those exclusively.
  3. Otherwise fall back to the env-based defaults
     (SLACK_WEBHOOK_URL, WEBHOOK_URL, SMTP_ENABLED + NOTIFICATION_EMAILS).

Each candidate channel is then gated by the alert_policy: a channel only
fires if it appears in policy.channels for the alert_type and the event
(opened/resolved) is allowed by the policy.

Failure on one channel does not block the others. Returns True if at
least one channel delivered successfully.

DB row config payloads:
  slack    : {"webhook_url": "https://hooks.slack.com/..."}
  webhook  : {"url": "https://example.com/notify"}
  email    : {"recipients": ["a@b.com", "c@d.com"]}
             SMTP credentials are still taken from settings.smtp_*
             because they are infrastructure, not policy.
  whatsapp : {"webhook_url": "https://...", "secret_token": "optional"}
             Delivered via WhatChimp Webhook Workflow. Phone routing is
             configured on the WhatChimp side, not here.
"""

from __future__ import annotations

import logging

import httpx

from app.core.alert_constants import AlertChannel, NotificationEvent, Severity
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.device import Device
from app.models.incident import Incident
from app.models.notification_channel import NotificationChannel
from app.services import (
    alert_formatter,
    email_service,
    notification_channel_service,
    whatsapp_service,
)
from app.services.alert_policy import get_policy_for_device, should_notify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal channel descriptor
# ---------------------------------------------------------------------------

class _ChannelTarget:
    """Single delivery target resolved from DB row or env fallback."""

    __slots__ = ("kind", "label", "url", "recipients", "secret_token")

    def __init__(
        self,
        kind: str,
        label: str,
        url: str | None = None,
        recipients: list[str] | None = None,
        secret_token: str | None = None,
    ) -> None:
        self.kind = kind          # "slack" | "webhook" | "email" | "whatsapp"
        self.label = label        # for logging
        self.url = url
        self.recipients = recipients or []
        self.secret_token = secret_token


async def _post_webhook(url: str, payload: dict) -> bool:
    """POST a JSON payload to a webhook URL. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 300:
                logger.warning("Webhook HTTP %d — %s", resp.status_code, url)
                return False
            return True
    except Exception as exc:
        logger.error("Webhook delivery failed (%s): %s", url, exc)
        return False


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------

def _channel_from_db_row(row: NotificationChannel) -> _ChannelTarget | None:
    """Convert a DB row to a runtime channel target. Returns None if unusable."""
    cfg = row.config or {}
    if row.channel_type == AlertChannel.SLACK:
        url = cfg.get("webhook_url")
        if not url:
            logger.warning("Channel %r missing 'webhook_url' in config", row.name)
            return None
        return _ChannelTarget("slack", f"db:{row.name}", url=url)
    if row.channel_type == AlertChannel.WEBHOOK:
        url = cfg.get("url")
        if not url:
            logger.warning("Channel %r missing 'url' in config", row.name)
            return None
        return _ChannelTarget("webhook", f"db:{row.name}", url=url)
    if row.channel_type == AlertChannel.EMAIL:
        recipients = cfg.get("recipients") or []
        if not recipients:
            logger.warning("Channel %r missing 'recipients' in config", row.name)
            return None
        return _ChannelTarget("email", f"db:{row.name}", recipients=list(recipients))
    if row.channel_type == AlertChannel.WHATSAPP:
        url = cfg.get("webhook_url")
        if not url:
            logger.warning("Channel %r missing 'webhook_url' in config", row.name)
            return None
        return _ChannelTarget(
            "whatsapp", f"db:{row.name}",
            url=url,
            secret_token=cfg.get("secret_token"),
        )
    logger.warning("Channel %r has unknown channel_type %r", row.name, row.channel_type)
    return None


def _channels_from_env() -> list[_ChannelTarget]:
    """Build the env-based channel list (current backward-compat behaviour)."""
    settings = get_settings()
    targets: list[_ChannelTarget] = []
    if settings.slack_webhook_url:
        targets.append(_ChannelTarget("slack", "env:SLACK_WEBHOOK_URL", url=settings.slack_webhook_url))
    if settings.webhook_url:
        targets.append(_ChannelTarget("webhook", "env:WEBHOOK_URL", url=settings.webhook_url))
    if settings.smtp_enabled and settings.notification_email_list:
        targets.append(_ChannelTarget(
            "email", "env:SMTP",
            recipients=list(settings.notification_email_list),
        ))
    return targets


async def _resolve_channels() -> list[_ChannelTarget]:
    """
    Pick channels from DB if any enabled row exists, else fall back to env.
    Uses a short-lived read-only session — does not interfere with caller's session.
    """
    try:
        async with async_session_factory() as session:
            rows = await notification_channel_service.list_channels(session, enabled_only=True)
    except Exception as exc:
        logger.error("Failed to load notification_channels from DB — using env fallback: %s", exc)
        return _channels_from_env()

    if not rows:
        return _channels_from_env()

    targets: list[_ChannelTarget] = []
    for row in rows:
        target = _channel_from_db_row(row)
        if target is not None:
            targets.append(target)
    return targets or _channels_from_env()


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
    if target.kind == "slack":
        payload = alert_formatter.format_for_slack(device, incident, event)
        return await _post_webhook(target.url, payload)
    if target.kind == "webhook":
        payload = alert_formatter.format_for_webhook(device, incident, event)
        return await _post_webhook(target.url, payload)
    if target.kind == "email":
        subject, text_body, html_body = alert_formatter.format_for_email(
            device, incident, event,
        )
        return await email_service.send_email(target.recipients, subject, text_body, html_body)
    if target.kind == "whatsapp":
        settings = get_settings()
        payload = alert_formatter.format_for_whatsapp(device, incident, event)
        return await whatsapp_service.send_whatsapp_alert(
            target.url,
            payload,
            test_mode=settings.whatsapp_test_mode,
            secret_token=target.secret_token,
        )
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
    for target in targets:
        if not should_notify(target.kind, policy, incident.severity, event):
            logger.debug(
                "Skipping %s (%s) — policy excludes channel for %s/%s",
                target.kind, target.label, incident.alert_type, event,
            )
            continue
        results.append(await _deliver(target, device, incident, event))

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
