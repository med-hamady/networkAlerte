"""
Tests for warning digest formatting and dispatch deferral.

Two angles:
  - alert_formatter.format_digest_for_email renders well-shaped payloads
  - notification_service._dispatch defers groupable warnings on opened
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.alert_constants import (
    AT_CCQ_LOW,
    AT_ROCKET_DOWN,
    AT_SIGNAL_LOW,
    NotificationEvent,
    Severity,
)
from app.core.alert_labels import alert_type_label
from app.services import alert_formatter, notification_service
from app.services.notification_service import _ChannelTarget


UTC = datetime.timezone.utc


def _device(name="LTU Rocket", ip="10.135.2.218", overrides=None):
    return SimpleNamespace(
        id=1, name=name, ip_address=ip, device_type="rocket",
        policy_overrides=overrides,
    )


def _incident(alert_type, severity=Severity.WARNING, **kwargs):
    base = dict(
        id=42, device_id=1, title="t", description=None,
        severity=severity, status="open", alert_type=alert_type,
        metric_name=None, metric_value=None, threshold_value=None,
        probable_cause=None,
        detected_at=datetime.datetime(2026, 4, 22, 14, 5, tzinfo=UTC),
        last_triggered_at=None, resolved_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# format_digest_for_email
# ---------------------------------------------------------------------------

def test_digest_email_returns_subject_text_html():
    items = [
        (_device(), _incident(AT_CCQ_LOW)),
        (_device("LR"),  _incident(AT_SIGNAL_LOW)),
    ]
    subject, text, html = alert_formatter.format_digest_for_email(items)
    assert "WARNINGS" in subject
    assert "2 alerte" in subject
    assert alert_type_label(AT_CCQ_LOW) in text
    assert alert_type_label(AT_SIGNAL_LOW) in text
    assert "<html>" in html


def test_digest_groups_by_alert_type_in_text():
    items = [
        (_device("Rocket A"), _incident(AT_CCQ_LOW, metric_name="ccq_pct", metric_value=40.0)),
        (_device("Rocket B"), _incident(AT_CCQ_LOW, metric_name="ccq_pct", metric_value=55.0)),
        (_device("LR 1"),     _incident(AT_SIGNAL_LOW, metric_name="signal_dbm", metric_value=-75.0)),
    ]
    text = alert_formatter._digest_text_body(items)
    assert "3 alerte(s)" in text
    assert alert_type_label(AT_CCQ_LOW) in text
    assert alert_type_label(AT_SIGNAL_LOW) in text
    assert "Rocket A" in text and "Rocket B" in text


def test_digest_handles_empty_list():
    text = alert_formatter._digest_text_body([])
    assert "Aucune" in text


# ---------------------------------------------------------------------------
# notification_service._dispatch defers groupable warnings on open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_defers_groupable_warning_on_open():
    """ccq_low (warning, groupable) opened → no immediate notification."""
    async def fake_resolve():
        return [_ChannelTarget("email", "db:opsmail", recipients=["a@x"])]

    delivered = []

    async def fake_deliver(target, device, incident, event):
        delivered.append(target.kind)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_opened(
            _device(),
            _incident(AT_CCQ_LOW, severity=Severity.WARNING),
        )

    assert ok is False
    assert delivered == []


@pytest.mark.asyncio
async def test_dispatch_critical_with_dynamic_severity_not_deferred():
    """radio_link_degraded with critical severity must still notify immediately."""
    async def fake_resolve():
        return [_ChannelTarget("email", "db:opsmail", recipients=["a@x"])]

    delivered = []

    async def fake_deliver(target, device, incident, event):
        delivered.append(target.kind)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_opened(
            _device(),
            _incident("radio_link_degraded", severity=Severity.CRITICAL),
        )

    assert ok is True
    assert delivered == ["email"]


@pytest.mark.asyncio
async def test_dispatch_resolved_for_groupable_not_deferred():
    """Recovery messages for groupable warnings still go out — not digested."""
    async def fake_resolve():
        return [_ChannelTarget("email", "db:opsmail", recipients=["a@x"])]

    delivered = []

    async def fake_deliver(target, device, incident, event):
        delivered.append((target.kind, event))
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_resolved(
            _device(),
            _incident(AT_CCQ_LOW, severity=Severity.WARNING, status="resolved"),
        )

    assert ok is True
    assert delivered == [("email", NotificationEvent.RESOLVED)]


@pytest.mark.asyncio
async def test_dispatch_critical_rocket_down_not_deferred():
    """rocket_down (critical, not groupable) → immediate notify."""
    async def fake_resolve():
        return [_ChannelTarget("email", "db:opsmail", recipients=["a@x"])]

    delivered = []

    async def fake_deliver(target, device, incident, event):
        delivered.append(target.kind)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_opened(
            _device(),
            _incident(AT_ROCKET_DOWN, severity=Severity.CRITICAL),
        )

    assert ok is True
    assert delivered == ["email"]
