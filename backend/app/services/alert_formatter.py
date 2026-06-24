"""
Alert formatter — uniform message generation for every notification channel.

Every alert message produced by the system goes through this module so that
operators see the same fields rendered the same way whether the alert lands
in a WhatsApp message or in a log line.

Public functions (all take a Device + Incident + lifecycle event):
    format_human_readable(device, incident, event)  -> str
    format_for_whatsapp(device, incident, event)    -> str

`event` is one of NotificationEvent.OPENED / NotificationEvent.RESOLVED.
"""

from __future__ import annotations

import datetime

from app.core.alert_constants import (
    AT_BATTERY_EXTERNAL_LOW,
    AT_BATTERY_INTERNAL_LOW,
    NotificationEvent,
    Severity,
)
from app.core.alert_labels import alert_type_label, metric_label
from app.models.device import Device
from app.models.incident import Incident

# Alert types whose human description carries operational context not present in
# the structured fields (charge % + estimated autonomy for the UISP Power
# batteries). For these we render the description as an extra line so the
# WhatsApp message exposes it; every other alert keeps the terse field layout.
_DESCRIPTION_ALERT_TYPES = {AT_BATTERY_INTERNAL_LOW, AT_BATTERY_EXTERNAL_LOW}

# ---------------------------------------------------------------------------
# Visual identity per severity
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI = {
    Severity.INFO:     "🔵",
    Severity.WARNING:  "🟡",
    Severity.CRITICAL: "🔴",
}
_SEVERITY_LABEL = {
    Severity.INFO:     "INFO",
    Severity.WARNING:  "WARNING",
    Severity.CRITICAL: "CRITICAL",
}
_RECOVERY_EMOJI = "✅"


def _severity_label(severity: str | None) -> str:
    return _SEVERITY_LABEL.get(severity or "", (severity or "?").upper())


def _severity_emoji(severity: str | None) -> str:
    return _SEVERITY_EMOJI.get(severity or "", "⚪")


def _fmt_dt(dt: datetime.datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_duration(start: datetime.datetime | None, end: datetime.datetime | None) -> str:
    if start is None or end is None:
        return "N/A"
    delta = end - start
    total = int(delta.total_seconds())
    if total < 0:
        return "N/A"
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min {seconds} s"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"


def _metric_line(incident: Incident) -> str | None:
    """Render the metric line if metric data is attached to the incident."""
    if incident.metric_name is None:
        return None
    parts = [metric_label(incident.metric_name)]
    if incident.metric_value is not None:
        parts.append(f"= {incident.metric_value}")
    if incident.threshold_value is not None:
        parts.append(f"(seuil {incident.threshold_value})")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Human-readable text block (used in logs, API .message, email plain text)
# ---------------------------------------------------------------------------

def format_human_readable(
    device: Device,
    incident: Incident,
    event: str = NotificationEvent.OPENED,
) -> str:
    """Single block of text suitable for logs, terminal output, or chat."""
    alert_label = alert_type_label(incident.alert_type)

    if event == NotificationEvent.RESOLVED:
        return (
            f"{_RECOVERY_EMOJI} RÉTABLI — {alert_label}\n"
            f"Équipement   : {device.name}\n"
            f"IP           : {device.ip_address}\n"
            f"Résolu à     : {_fmt_dt(incident.resolved_at)}\n"
            f"Durée        : {_fmt_duration(incident.detected_at, incident.resolved_at)}"
        )

    severity = incident.severity or Severity.WARNING
    emoji = _severity_emoji(severity)
    label = _severity_label(severity)

    lines = [
        f"{emoji} {label} — {alert_label}",
        f"Équipement   : {device.name} ({device.device_type})",
        f"IP           : {device.ip_address}",
    ]
    metric = _metric_line(incident)
    if metric:
        lines.append(f"Métrique     : {metric}")
    if incident.alert_type in _DESCRIPTION_ALERT_TYPES and incident.description:
        lines.append(f"Détail       : {incident.description}")
    lines.append(f"Début        : {_fmt_dt(incident.detected_at)}")
    return "\n".join(lines)


def format_for_whatsapp(
    device: Device,
    incident: Incident,
    event: str = NotificationEvent.OPENED,
) -> str:
    """Return a single text block for the WhatsApp channel.

    Reuses format_human_readable (already French + emoji, no HTML) and wraps the
    first line — the severity/alert title — in WhatsApp bold markdown (*...*) so
    it stands out in the group chat. No subject line: WhatsApp messages have no
    subject, the first line is the headline.
    """
    body = format_human_readable(device, incident, event)
    head, _, rest = body.partition("\n")
    head = f"*{head}*"
    return f"{head}\n{rest}" if rest else head


# ---------------------------------------------------------------------------
# Warning digest — batched notification for groupable warnings
# ---------------------------------------------------------------------------

def _digest_line(device: Device, incident: Incident) -> str:
    """One-line summary of a single warning inside a digest."""
    parts = [
        f"• {alert_type_label(incident.alert_type)}",
        f"{device.name} ({device.ip_address})",
    ]
    metric = _metric_line(incident)
    if metric:
        parts.append(metric)
    return " — ".join(parts)


def _digest_text_body(items: list[tuple[Device, Incident]]) -> str:
    if not items:
        return "Aucune warning active."
    lines = [
        f"🟡 Warnings digest — {len(items)} alerte(s) en cours",
        "",
    ]
    by_type: dict[str, list[tuple[Device, Incident]]] = {}
    for dev, inc in items:
        by_type.setdefault(inc.alert_type or "unknown", []).append((dev, inc))
    for alert_type, group in sorted(by_type.items()):
        lines.append(f"[{alert_type_label(alert_type)}] ({len(group)})")
        for dev, inc in group:
            lines.append(_digest_line(dev, inc))
        lines.append("")
    return "\n".join(lines).rstrip()


def format_digest_for_whatsapp(items: list[tuple[Device, Incident]]) -> str:
    """Return a single text block for a warning digest sent over WhatsApp."""
    return _digest_text_body(items)
