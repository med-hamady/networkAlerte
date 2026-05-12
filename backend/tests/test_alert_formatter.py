"""
Unit tests for alert_formatter.py — pure Python, no DB required.

Validates:
  - human-readable text contains all the operationally critical fields
  - email returns subject + plain body + html body, all non-empty
  - recovery format includes the duration
  - opened format always exposes the recommended_action
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

from app.core.alert_constants import (
    AT_CCQ_LOW,
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
        device_type="rocket",
        radio_tech="ltu",
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
