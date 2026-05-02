"""
Unit tests for alert_formatter.py — pure Python, no DB required.

Validates:
  - human-readable text contains all the operationally critical fields
  - Slack payload is a non-empty Slack-shaped dict
  - webhook payload includes the policy-derived fields the dashboard needs
  - email returns subject + plain body + html body, all non-empty
  - recovery format includes the duration
  - opened format always exposes the recommended_action
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

from app.core.alert_constants import (
    AT_CCQ_LOW,
    AT_RADIO_LINK_DEGRADED,
    AT_ROCKET_DOWN,
    NotificationEvent,
    Severity,
)
from app.services import alert_formatter
from app.services.alert_policy import get_policy


UTC = datetime.timezone.utc


def _device(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        name="LTU Rocket",
        ip_address="10.135.2.218",
        device_type="ltu_rocket",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _incident(**overrides) -> SimpleNamespace:
    base = dict(
        id=42,
        device_id=1,
        title="ALERTE CRITIQUE : LTU Rocket indisponible",
        description="ne répond pas au ping ICMP",
        severity=Severity.CRITICAL,
        status="open",
        alert_type=AT_ROCKET_DOWN,
        metric_name=None,
        metric_value=None,
        threshold_value=None,
        probable_cause="switch_down",
        detected_at=datetime.datetime(2026, 4, 22, 14, 5, 12, tzinfo=UTC),
        last_triggered_at=datetime.datetime(2026, 4, 22, 14, 6, 12, tzinfo=UTC),
        resolved_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# format_human_readable
# ---------------------------------------------------------------------------

def test_human_readable_opened_critical_contains_required_fields():
    out = alert_formatter.format_human_readable(
        _device(), _incident(), NotificationEvent.OPENED,
    )
    assert "CRITICAL" in out
    assert AT_ROCKET_DOWN in out
    assert "LTU Rocket" in out
    assert "10.135.2.218" in out
    assert "switch_down" in out  # probable_cause
    assert get_policy(AT_ROCKET_DOWN).recommended_action.split(" · ")[0] in out


def test_human_readable_warning_includes_metric_line():
    inc = _incident(
        alert_type=AT_CCQ_LOW,
        severity=Severity.WARNING,
        metric_name="ccq_pct",
        metric_value=40.0,
        threshold_value=75.0,
        probable_cause="radio_quality_issue",
    )
    out = alert_formatter.format_human_readable(_device(), inc)
    assert "WARNING" in out
    assert "ccq_pct" in out
    assert "40" in out
    assert "75" in out


def test_human_readable_resolved_includes_duration():
    inc = _incident(
        status="resolved",
        resolved_at=datetime.datetime(2026, 4, 22, 14, 12, 3, tzinfo=UTC),
    )
    out = alert_formatter.format_human_readable(
        _device(), inc, NotificationEvent.RESOLVED,
    )
    assert "RECOVERY" in out
    assert "résolu" in out
    # 14:05:12 → 14:12:03 = 6 min 51 s
    assert "6 min" in out
    assert "51 s" in out


def test_human_readable_resolved_handles_missing_dates():
    inc = _incident(status="resolved", detected_at=None, resolved_at=None)
    out = alert_formatter.format_human_readable(
        _device(), inc, NotificationEvent.RESOLVED,
    )
    assert "N/A" in out


# ---------------------------------------------------------------------------
# format_for_slack
# ---------------------------------------------------------------------------

def test_slack_opened_returns_text_payload():
    payload = alert_formatter.format_for_slack(
        _device(), _incident(), NotificationEvent.OPENED,
    )
    assert isinstance(payload, dict)
    assert "text" in payload
    text = payload["text"]
    assert "CRITICAL" in text
    assert AT_ROCKET_DOWN in text
    assert "LTU Rocket" in text
    assert "10.135.2.218" in text


def test_slack_resolved_short_message():
    inc = _incident(
        status="resolved",
        resolved_at=datetime.datetime(2026, 4, 22, 14, 12, 3, tzinfo=UTC),
    )
    payload = alert_formatter.format_for_slack(
        _device(), inc, NotificationEvent.RESOLVED,
    )
    text = payload["text"]
    assert "RECOVERY" in text
    assert "LTU Rocket" in text
    assert "résolu" in text


# ---------------------------------------------------------------------------
# format_for_webhook
# ---------------------------------------------------------------------------

def test_webhook_payload_is_json_serializable():
    payload = alert_formatter.format_for_webhook(_device(), _incident())
    # Must round-trip through json.dumps without errors
    json.dumps(payload)


def test_webhook_payload_carries_policy_fields():
    payload = alert_formatter.format_for_webhook(_device(), _incident())
    assert payload["event"] == "incident_opened"
    assert payload["alert_type"] == AT_ROCKET_DOWN
    assert payload["severity"] == Severity.CRITICAL
    assert payload["device_name"] == "LTU Rocket"
    assert payload["device_ip"] == "10.135.2.218"
    assert payload["device_type"] == "ltu_rocket"
    assert payload["recommended_action"]
    assert payload["notify_immediately"] is True
    assert "slack" in payload["notification_channel_policy"]
    assert payload["message"]
    assert payload["probable_cause"] == "switch_down"


def test_webhook_resolved_includes_duration_field():
    inc = _incident(
        status="resolved",
        resolved_at=datetime.datetime(2026, 4, 22, 14, 12, 3, tzinfo=UTC),
    )
    payload = alert_formatter.format_for_webhook(
        _device(), inc, NotificationEvent.RESOLVED,
    )
    assert payload["event"] == "incident_resolved"
    assert "duration" in payload
    assert "6 min" in payload["duration"]


def test_webhook_dynamic_severity_resolves_notify_immediately():
    """radio_link_degraded inherits notify_immediately from incident severity."""
    inc_warn = _incident(alert_type=AT_RADIO_LINK_DEGRADED, severity=Severity.WARNING)
    inc_crit = _incident(alert_type=AT_RADIO_LINK_DEGRADED, severity=Severity.CRITICAL)

    p_warn = alert_formatter.format_for_webhook(_device(), inc_warn)
    p_crit = alert_formatter.format_for_webhook(_device(), inc_crit)

    assert p_warn["notify_immediately"] is False
    assert p_crit["notify_immediately"] is True


# ---------------------------------------------------------------------------
# format_for_email
# ---------------------------------------------------------------------------

def test_email_opened_returns_subject_text_and_html():
    subject, text, html = alert_formatter.format_for_email(
        _device(), _incident(), NotificationEvent.OPENED,
    )
    assert subject
    assert "CRITICAL" in subject
    assert "LTU Rocket" in subject
    assert text
    assert html
    assert "<html>" in html
    # Recommended action must appear in the HTML body
    assert "Action recommandée" in html
    first_action_token = (
        get_policy(AT_ROCKET_DOWN).recommended_action.split(" · ")[0]
    )
    assert first_action_token in html


def test_email_resolved_subject_and_duration_in_html():
    inc = _incident(
        status="resolved",
        resolved_at=datetime.datetime(2026, 4, 22, 14, 12, 3, tzinfo=UTC),
    )
    subject, text, html = alert_formatter.format_for_email(
        _device(), inc, NotificationEvent.RESOLVED,
    )
    assert "RÉSOLU" in subject
    assert "6 min" in html
    assert "RECOVERY" in text


# ---------------------------------------------------------------------------
# Cross-channel consistency
# ---------------------------------------------------------------------------

def test_all_channels_show_same_alert_type_and_ip():
    dev = _device()
    inc = _incident()
    text = alert_formatter.format_human_readable(dev, inc)
    slack = alert_formatter.format_for_slack(dev, inc)["text"]
    webhook = alert_formatter.format_for_webhook(dev, inc)
    _, email_text, email_html = alert_formatter.format_for_email(dev, inc)

    for blob in (text, slack, email_text, email_html):
        assert AT_ROCKET_DOWN in blob
        assert dev.ip_address in blob

    assert webhook["alert_type"] == AT_ROCKET_DOWN
    assert webhook["device_ip"] == dev.ip_address
