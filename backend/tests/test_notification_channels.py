"""
Tests for the DB-backed notification_channels resolution.

We exercise the channel-resolution logic of notification_service without
touching the real DB by mocking the channel-loading step. The actual
delivery (HTTP/SMTP) is also mocked so the tests are pure-Python.
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
        id=1, name="LTU Rocket", ip_address="10.135.2.218", device_type="ltu_rocket",
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
        probable_cause=None,
        detected_at=datetime.datetime(2026, 4, 22, 14, 5, 12, tzinfo=datetime.timezone.utc),
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

def test_channel_from_db_row_slack():
    target = _channel_from_db_row(
        _row("ops-slack", AlertChannel.SLACK, {"webhook_url": "https://hooks.slack.com/X"}),
    )
    assert target is not None
    assert target.kind == "slack"
    assert target.url == "https://hooks.slack.com/X"


def test_channel_from_db_row_webhook():
    target = _channel_from_db_row(
        _row("siem", AlertChannel.WEBHOOK, {"url": "https://siem.example.com/in"}),
    )
    assert target is not None
    assert target.kind == "webhook"
    assert target.url == "https://siem.example.com/in"


def test_channel_from_db_row_email():
    target = _channel_from_db_row(
        _row("oncall", AlertChannel.EMAIL, {"recipients": ["a@x.com", "b@x.com"]}),
    )
    assert target is not None
    assert target.kind == "email"
    assert target.recipients == ["a@x.com", "b@x.com"]


def test_channel_from_db_row_missing_url_returns_none():
    target = _channel_from_db_row(_row("broken", AlertChannel.SLACK, {}))
    assert target is None


def test_channel_from_db_row_unknown_type_returns_none():
    target = _channel_from_db_row(_row("oddly", "carrier-pigeon", {}))
    assert target is None


# ---------------------------------------------------------------------------
# Env fallback
# ---------------------------------------------------------------------------

def test_env_fallback_picks_slack_url(monkeypatch):
    fake_settings = SimpleNamespace(
        slack_webhook_url="https://hooks.slack.com/E",
        webhook_url=None,
        smtp_enabled=False,
        notification_email_list=[],
    )
    monkeypatch.setattr(
        notification_service, "get_settings", lambda: fake_settings,
    )
    targets = _channels_from_env()
    assert any(t.kind == "slack" for t in targets)
    assert all(t.kind != "email" for t in targets)


def test_env_fallback_picks_email_when_smtp_enabled(monkeypatch):
    fake_settings = SimpleNamespace(
        slack_webhook_url=None,
        webhook_url=None,
        smtp_enabled=True,
        notification_email_list=["ops@example.com"],
    )
    monkeypatch.setattr(
        notification_service, "get_settings", lambda: fake_settings,
    )
    targets = _channels_from_env()
    assert any(t.kind == "email" for t in targets)


# ---------------------------------------------------------------------------
# Dispatch — DB-first, env fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_uses_db_channels_when_present():
    db_targets = [_ChannelTarget("slack", "db:slackA", url="https://x")]

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
    assert delivered_via == ["db:slackA"]


@pytest.mark.asyncio
async def test_dispatch_skips_channels_outside_policy():
    """
    Channels not listed in policy.channels are skipped at dispatch.

    Uses an unknown alert_type so the fallback policy applies (channels=webhook
    only, not groupable). With slack + webhook targets, only webhook should fire.
    """
    targets = [
        _ChannelTarget("slack", "db:slack", url="https://x"),
        _ChannelTarget("webhook", "db:webhook", url="https://y"),
    ]
    delivered_kinds: list[str] = []

    async def fake_resolve():
        return targets

    async def fake_deliver(target, device, incident, event):
        delivered_kinds.append(target.kind)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_opened(
            _device(),
            _incident(alert_type="not_a_real_alert", severity=Severity.WARNING),
        )

    assert ok is True
    assert "webhook" in delivered_kinds
    assert "slack" not in delivered_kinds


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_no_channels():
    async def empty_resolve():
        return []

    with patch.object(notification_service, "_resolve_channels", empty_resolve):
        ok = await notification_service.notify_incident_opened(_device(), _incident())

    assert ok is False


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_all_channels_blocked_by_policy():
    """
    Email-only target + policy that does not list email → no delivery.

    Uses the fallback policy (unknown alert_type, channels=webhook only) to
    exercise channel exclusion at dispatch.
    """
    targets = [_ChannelTarget("email", "db:email", recipients=["a@x"])]

    delivered: list[str] = []

    async def fake_resolve():
        return targets

    async def fake_deliver(target, device, incident, event):
        delivered.append(target.kind)
        return True

    with (
        patch.object(notification_service, "_resolve_channels", fake_resolve),
        patch.object(notification_service, "_deliver", fake_deliver),
    ):
        ok = await notification_service.notify_incident_opened(
            _device(),
            _incident(alert_type="not_a_real_alert", severity=Severity.WARNING),
        )

    assert ok is False
    assert delivered == []


@pytest.mark.asyncio
async def test_dispatch_resolved_event_for_critical():
    targets = [_ChannelTarget("slack", "db:slack", url="https://x")]
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
