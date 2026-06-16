"""
Unit tests for alert_formatter.py — pure Python, no DB required.

Validates:
  - human-readable text contains all the operationally critical fields
  - WhatsApp format bolds the headline and keeps the body
  - recovery format includes the duration
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
from app.core.alert_labels import alert_type_label, metric_label
from app.services import alert_formatter

UTC = datetime.UTC


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
    assert alert_type_label(AT_ROCKET_DOWN) in out
    assert "LTU Rocket" in out
    assert "10.135.2.218" in out


def test_human_readable_warning_includes_metric_line():
    inc = _incident(
        alert_type=AT_CCQ_LOW,
        severity=Severity.WARNING,
        metric_name="ccq_pct",
        metric_value=40.0,
        threshold_value=75.0,
    )
    out = alert_formatter.format_human_readable(_device(), inc)
    assert "WARNING" in out
    assert metric_label("ccq_pct") in out
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
    assert "RÉTABLI" in out
    assert alert_type_label(AT_ROCKET_DOWN) in out
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
# format_for_whatsapp
# ---------------------------------------------------------------------------

def test_whatsapp_opened_bolds_headline_and_keeps_body():
    out = alert_formatter.format_for_whatsapp(
        _device(), _incident(), NotificationEvent.OPENED,
    )
    # First line is the bold headline (*...*), the rest is the body.
    head, _, rest = out.partition("\n")
    assert head.startswith("*") and head.endswith("*")
    assert "LTU Rocket" in rest


def test_whatsapp_resolved_includes_duration():
    inc = _incident(
        status="resolved",
        resolved_at=datetime.datetime(2026, 4, 22, 14, 12, 3, tzinfo=UTC),
    )
    out = alert_formatter.format_for_whatsapp(
        _device(), inc, NotificationEvent.RESOLVED,
    )
    assert "RÉTABLI" in out
    assert "6 min" in out
