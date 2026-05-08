"""
SMTP email service — async email delivery via aiosmtplib.

Supports:
  - STARTTLS (port 587, smtp_use_tls=True)   ← Gmail, Office 365, OVH, etc.
  - SSL/TLS  (port 465, smtp_use_ssl=True)
  - Plain    (port 25,  both False)           ← serveur local / relay interne

Configuration via .env:
  SMTP_ENABLED=true
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USERNAME=votre@email.com
  SMTP_PASSWORD=mot_de_passe_app
  SMTP_FROM_EMAIL=supervision@company.com
  SMTP_FROM_NAME=Network Supervisor
  SMTP_USE_TLS=true
  NOTIFICATION_EMAILS=admin@company.com,ops@company.com

Subject + HTML body are produced by alert_formatter so the wording stays in
sync with the human-readable / log messages.
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.alert_constants import NotificationEvent
from app.core.config import get_settings
from app.models.device import Device
from app.models.incident import Incident
from app.services import alert_formatter

logger = logging.getLogger(__name__)


async def send_email(
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    """
    Send an email to one or more recipients.
    Returns True on success, False on any delivery failure.
    """
    settings = get_settings()

    if not settings.smtp_enabled:
        logger.debug("SMTP disabled — skipping email")
        return False

    if not to:
        logger.debug("No recipients — skipping email")
        return False

    if not settings.smtp_from_email:
        logger.warning("SMTP_FROM_EMAIL not configured — cannot send email")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = (
        f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        if settings.smtp_from_name else settings.smtp_from_email
    )
    msg["To"] = ", ".join(to)

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_use_ssl,
            start_tls=settings.smtp_use_tls,
        )
        logger.info("Email sent — subject='%s' to=%s", subject, to)
        return True

    except aiosmtplib.SMTPException as exc:
        logger.error("SMTP delivery failed — %s", exc)
        return False
    except Exception as exc:
        logger.error("Email unexpected error — %s", exc)
        return False


async def notify_incident_opened_email(device: Device, incident: Incident) -> bool:
    """Send an incident-opened email to all configured recipients."""
    settings = get_settings()
    recipients = settings.notification_email_list
    if not recipients:
        return False

    subject, body_text, body_html = alert_formatter.format_for_email(
        device, incident, NotificationEvent.OPENED,
    )
    return await send_email(recipients, subject, body_text, body_html)


async def notify_incident_resolved_email(device: Device, incident: Incident) -> bool:
    """Send an incident-resolved email to all configured recipients."""
    settings = get_settings()
    recipients = settings.notification_email_list
    if not recipients:
        return False

    subject, body_text, body_html = alert_formatter.format_for_email(
        device, incident, NotificationEvent.RESOLVED,
    )
    return await send_email(recipients, subject, body_text, body_html)
