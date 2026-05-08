"""
Alert formatter — uniform message generation for every notification channel.

Every alert message produced by the system goes through this module so that
operators see the same fields rendered the same way whether the alert lands
in an email or in a log line.

Public functions (all take a Device + Incident + lifecycle event):
    format_human_readable(device, incident, event)  -> str
    format_for_email(device, incident, event)       -> tuple[subject, text, html]

`event` is one of NotificationEvent.OPENED / NotificationEvent.RESOLVED.
"""

from __future__ import annotations

import datetime

from app.core.alert_constants import (
    NotificationEvent,
    Severity,
)
from app.models.device import Device
from app.models.incident import Incident
from app.services.alert_policy import get_policy

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
_SEVERITY_COLOR = {
    Severity.INFO:     "#3498db",
    Severity.WARNING:  "#f39c12",
    Severity.CRITICAL: "#e74c3c",
}
_RECOVERY_EMOJI = "✅"
_RECOVERY_COLOR = "#27ae60"


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
    parts = [f"{incident.metric_name}"]
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
    policy = get_policy(incident.alert_type)
    alert_type = incident.alert_type or "unknown"

    if event == NotificationEvent.RESOLVED:
        return (
            f"{_RECOVERY_EMOJI} RECOVERY — {alert_type} résolu\n"
            f"Équipement   : {device.name}\n"
            f"IP           : {device.ip_address}\n"
            f"Résolu à     : {_fmt_dt(incident.resolved_at)}\n"
            f"Durée        : {_fmt_duration(incident.detected_at, incident.resolved_at)}"
        )

    severity = incident.severity or Severity.WARNING
    emoji = _severity_emoji(severity)
    label = _severity_label(severity)

    lines = [
        f"{emoji} {label} — {alert_type}",
        f"Équipement   : {device.name} ({device.device_type})",
        f"IP           : {device.ip_address}",
    ]
    metric = _metric_line(incident)
    if metric:
        lines.append(f"Métrique     : {metric}")
    if incident.probable_cause:
        lines.append(f"Cause probable: {incident.probable_cause}")
    lines.append(f"Début        : {_fmt_dt(incident.detected_at)}")
    lines.append(f"Action       : {policy.recommended_action}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email — subject + html body
# ---------------------------------------------------------------------------

def _email_subject(device: Device, incident: Incident, event: str) -> str:
    alert_type = incident.alert_type or "incident"
    if event == NotificationEvent.RESOLVED:
        return f"[RÉSOLU] {device.name} : {alert_type}"
    label = _severity_label(incident.severity)
    return f"[{label}] {alert_type} — {device.name}"


def _email_html_opened(device: Device, incident: Incident) -> str:
    policy = get_policy(incident.alert_type)
    severity = incident.severity or Severity.WARNING
    color = _SEVERITY_COLOR.get(severity, "#95a5a6")
    label = _severity_label(severity)
    alert_type = incident.alert_type or "incident"

    metric = _metric_line(incident)
    metric_row = (
        f"<tr><td style='padding:6px 0;color:#555;'>Métrique</td>"
        f"<td style='padding:6px 0;'>{metric}</td></tr>"
        if metric else ""
    )
    cause_row = (
        f"<tr><td style='padding:6px 0;color:#555;'>Cause probable</td>"
        f"<td style='padding:6px 0;'>{incident.probable_cause}</td></tr>"
        if incident.probable_cause else ""
    )
    desc_row = (
        f"<tr><td style='padding:6px 0;color:#555;'>Description</td>"
        f"<td style='padding:6px 0;'>{incident.description}</td></tr>"
        if incident.description else ""
    )

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:{color};padding:20px 30px;">
      <h2 style="color:#fff;margin:0;">{_severity_emoji(severity)} {label} — {alert_type}</h2>
    </div>
    <div style="padding:30px;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:6px 0;color:#555;width:160px;">Équipement</td>
            <td style="padding:6px 0;"><strong>{device.name}</strong></td></tr>
        <tr><td style="padding:6px 0;color:#555;">Adresse IP</td>
            <td style="padding:6px 0;">{device.ip_address}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Type</td>
            <td style="padding:6px 0;">{device.device_type}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Sévérité</td>
            <td style="padding:6px 0;">
              <span style="background:{color};color:#fff;padding:2px 8px;
                           border-radius:4px;font-size:12px;">{label}</span>
            </td></tr>
        {metric_row}
        {cause_row}
        {desc_row}
        <tr><td style="padding:6px 0;color:#555;">Détecté le</td>
            <td style="padding:6px 0;">{_fmt_dt(incident.detected_at)}</td></tr>
      </table>

      <div style="margin-top:20px;padding:15px;background:#fafafa;border-left:4px solid {color};">
        <strong style="color:#333;">Action recommandée</strong><br>
        <span style="color:#555;font-size:13px;">{policy.recommended_action}</span>
      </div>

      <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
      <p style="color:#888;font-size:12px;margin:0;">
        Ce message est généré automatiquement par Network Supervisor.<br>
        Connectez-vous au dashboard pour gérer cet incident.
      </p>
    </div>
  </div>
</body>
</html>"""


def _email_html_resolved(device: Device, incident: Incident) -> str:
    alert_type = incident.alert_type or "incident"
    duration = _fmt_duration(incident.detected_at, incident.resolved_at)
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:{_RECOVERY_COLOR};padding:20px 30px;">
      <h2 style="color:#fff;margin:0;">{_RECOVERY_EMOJI} RECOVERY — {alert_type} résolu</h2>
    </div>
    <div style="padding:30px;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:6px 0;color:#555;width:160px;">Équipement</td>
            <td style="padding:6px 0;"><strong>{device.name}</strong></td></tr>
        <tr><td style="padding:6px 0;color:#555;">Adresse IP</td>
            <td style="padding:6px 0;">{device.ip_address}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Résolu le</td>
            <td style="padding:6px 0;">{_fmt_dt(incident.resolved_at)}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Durée</td>
            <td style="padding:6px 0;">{duration}</td></tr>
      </table>
      <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
      <p style="color:#888;font-size:12px;margin:0;">
        Ce message est généré automatiquement par Network Supervisor.
      </p>
    </div>
  </div>
</body>
</html>"""


def format_for_email(
    device: Device,
    incident: Incident,
    event: str = NotificationEvent.OPENED,
) -> tuple[str, str, str]:
    """
    Return (subject, plain_text_body, html_body) for the email channel.

    plain_text_body falls back to the same content as format_human_readable so
    that mail clients without HTML rendering still get a readable message.
    """
    subject = _email_subject(device, incident, event)
    text_body = format_human_readable(device, incident, event)
    if event == NotificationEvent.RESOLVED:
        html_body = _email_html_resolved(device, incident)
    else:
        html_body = _email_html_opened(device, incident)
    return subject, text_body, html_body


# ---------------------------------------------------------------------------
# Warning digest — batched notification for groupable warnings
# ---------------------------------------------------------------------------

def _digest_line(device: Device, incident: Incident) -> str:
    """One-line summary of a single warning inside a digest."""
    parts = [
        f"• {incident.alert_type or 'unknown'}",
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
        lines.append(f"[{alert_type}] ({len(group)})")
        for dev, inc in group:
            lines.append(_digest_line(dev, inc))
        lines.append("")
    return "\n".join(lines).rstrip()


def _digest_html_body(items: list[tuple[Device, Incident]]) -> str:
    by_type: dict[str, list[tuple[Device, Incident]]] = {}
    for dev, inc in items:
        by_type.setdefault(inc.alert_type or "unknown", []).append((dev, inc))

    sections = []
    for alert_type, group in sorted(by_type.items()):
        rows = "".join(
            f"<tr><td style='padding:4px 0;color:#555;'>{dev.name}</td>"
            f"<td style='padding:4px 8px;'>{dev.ip_address}</td>"
            f"<td style='padding:4px 0;'>{_metric_line(inc) or '—'}</td></tr>"
            for dev, inc in group
        )
        sections.append(f"""
        <h3 style="color:#333;margin-top:20px;margin-bottom:8px;">
          {alert_type} <span style="color:#888;font-size:13px;">({len(group)})</span>
        </h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">{rows}</table>
        """)
    body_sections = "".join(sections)

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
  <div style="max-width:680px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:#f39c12;padding:20px 30px;">
      <h2 style="color:#fff;margin:0;">🟡 Warnings digest — {len(items)} alerte(s)</h2>
    </div>
    <div style="padding:30px;">
      {body_sections}
      <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
      <p style="color:#888;font-size:12px;margin:0;">
        Récap groupé des alertes warning persistantes — Network Supervisor.
      </p>
    </div>
  </div>
</body>
</html>"""


def format_digest_for_email(
    items: list[tuple[Device, Incident]],
) -> tuple[str, str, str]:
    """Return (subject, plain_text_body, html_body) for a warning digest email."""
    subject = f"[WARNINGS] {len(items)} alerte(s) actives — Network Supervisor"
    return subject, _digest_text_body(items), _digest_html_body(items)
