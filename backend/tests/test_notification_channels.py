"""
Tests for the DB-backed notification_channels resolution.

We exercise the channel-resolution logic of notification_service without
touching the real DB by mocking the channel-loading step. The actual
delivery (SMTP) is also mocked so the tests are pure-Python.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.alert_constants import (
    AT_ROCKET_DOWN,
    AlertChannel,
    NotificationEvent,
    Severity,
)
from app.services import notification_service
from app.services.notification_service import (
    _channel_from_db_row,
    _channels_from_env,
    _ChannelTarget,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _device():
    return SimpleNamespace(
        id=1, name="LTU Rocket", ip_address="10.135.2.218", device_type="rocket",
    )


def _incident(alert_type=AT_ROCKET_DOWN, severity=Severity.CRITICAL):
    import datetime
    return SimpleNamespace(
        id=42,
        device_id=1,
        title="Test",
        description=None,
        severity=severity,
        status="open",
        alert_type=alert_type,
        metric_name=None,
        metric_value=None,
        threshold_value=None,
        detected_at=datetime.datetime(2026, 4, 22, 14, 5, 12, tzinfo=datetime.UTC),
        last_triggered_at=None,
        resolved_at=None,
    )


def _row(name, channel_type, config, enabled=True):
    return SimpleNamespace(
        id=1, name=name, channel_type=channel_type, config=config, enabled=enabled,
    )


# ---------------------------------------------------------------------------
# DB row → ChannelTarget
# ---------------------------------------------------------------------------

def test_channel_from_db_row_email():
    target = _channel_from_db_row(
        _row("oncall", AlertChannel.EMAIL, {"recipients": ["a@x.com", "b@x.com"]}),
    )
    assert target is not None
    assert target.kind == "email"
    assert target.recipients == ["a@x.com", "b@x.com"]


def test_channel_from_db_row_missing_recipients_returns_none():
    target = _channel_from_db_row(_row("broken", AlertChannel.EMAIL, {}))
    assert target is None


def test_channel_from_db_row_unknown_type_returns_none():
    target = _channel_from_db_row(_row("oddly", "carrier-pigeon", {}))
    assert target is None


# ---------------------------------------------------------------------------
# Env fallback
# ---------------------------------------------------------------------------

def test_env_fallback_picks_email_when_smtp_enabled(monkeypatch):
    fake_settings = SimpleNamespace(
        smtp_enabled=True,
        notification_email_list=["ops@example.com"],
    )
    monkeypatch.setattr(
        notification_service, "get_settings", lambda: fake_settings,
    )
    targets = _channels_from_env()
    assert any(t.kind == "email" for t in targets)


def test_env_fallback_empty_when_smtp_disabled(monkeypatch):
    fake_settings = SimpleNamespace(
        smtp_enabled=False,
        notification_email_list=[],
    )
    monkeypatch.setattr(
        notification_service, "get_settings", lambda: fake_settings,
    )
    targets = _channels_from_env()
    assert targets == []


# ---------------------------------------------------------------------------
# Dispatch — DB-first, env fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_uses_db_channels_when_present():
    db_targets = [_ChannelTarget("email", "db:opsmail", recipients=["a@x"])]

    delivered_via: list[str] = []

    async def fake_resolve():
        return db_targets

    async def fake_deliver(target, device, incident, event):
        delivered_via.append(target.label)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_opened(_device(), _incident())

    assert ok is True
    assert delivered_via == ["db:opsmail"]


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_no_channels():
    async def empty_resolve():
        return []

    with patch.object(notification_service, "_resolve_channels", empty_resolve):
        ok = await notification_service.notify_incident_opened(_device(), _incident())

    assert ok is False


@pytest.mark.asyncio
async def test_dispatch_resolved_event_for_critical():
    targets = [_ChannelTarget("email", "db:opsmail", recipients=["a@x"])]
    seen_events: list[str] = []

    async def fake_resolve():
        return targets

    async def fake_deliver(target, device, incident, event):
        seen_events.append(event)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        await notification_service.notify_incident_resolved(_device(), _incident())

    assert seen_events == [NotificationEvent.RESOLVED]
