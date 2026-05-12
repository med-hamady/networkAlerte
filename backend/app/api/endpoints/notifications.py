"""
Notifications endpoints.

GET /notifications/         — list alert records (with incident + device info).
POST /notifications/test-email — send a test email to all configured recipients.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import Severity
from app.core.config import get_settings
from app.db.session import get_db
from app.models.alert import Alert
from app.models.device import Device
from app.models.incident import Incident
from app.schemas.alert import AlertReadEnriched
from app.services.email_service import send_email

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[AlertReadEnriched], summary="List notification records")
async def list_alerts(
    status: str | None = Query(
        None,
        description="Filter: sent, failed, pending, pending_digest — omit for all",
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AlertReadEnriched]:
    """
    Return all alert records ordered by most recent first.

    Sources:
    - alerts table: immediate notifications (sent/failed) and digested warnings.
    - incidents table: open warning incidents not yet included in a digest
      (is_pending_digest=True).
    """
    results: list[AlertReadEnriched] = []

    # ── 1. Alert table (sent / failed / digested) ────────────────────────────
    if status != "pending_digest":
        alerts_stmt = (
            select(
                Alert.id,
                Alert.incident_id,
                Alert.channel_id,
                Alert.message,
                Alert.status,
                Alert.sent_at,
                Alert.created_at,
                Incident.title.label("incident_title"),
                Incident.severity.label("incident_severity"),
                Incident.alert_type.label("incident_alert_type"),
                Incident.device_id.label("device_id"),
                Device.name.label("device_name"),
                Device.ip_address.label("device_ip"),
            )
            .outerjoin(Incident, Alert.incident_id == Incident.id)
            .outerjoin(Device, Incident.device_id == Device.id)
            .order_by(desc(Alert.created_at))
        )
        if status:
            alerts_stmt = alerts_stmt.where(Alert.status == status)

        alert_rows = await db.execute(alerts_stmt)
        for row in alert_rows.mappings().all():
            results.append(AlertReadEnriched(**row, is_pending_digest=False))

    # ── 2. Pending digest warnings (open warnings not yet digested) ──────────
    if status in (None, "pending_digest"):
        pending_stmt = (
            select(
                Incident.id.label("incident_id"),
                Incident.title.label("message"),
                Incident.severity.label("incident_severity"),
                Incident.alert_type.label("incident_alert_type"),
                Incident.device_id.label("device_id"),
                Incident.created_at,
                Device.name.label("device_name"),
                Device.ip_address.label("device_ip"),
            )
            .join(Device, Incident.device_id == Device.id)
            .where(
                Incident.severity == Severity.WARNING,
                Incident.digested_at.is_(None),
                Incident.status == "open",
            )
            .order_by(desc(Incident.created_at))
        )
        pending_rows = await db.execute(pending_stmt)
        for row in pending_rows.mappings().all():
            results.append(AlertReadEnriched(
                id=-(row["incident_id"]),   # negative to avoid collision with alert IDs
                incident_id=row["incident_id"],
                channel_id=None,
                message=row["message"],
                status="pending_digest",
                sent_at=None,
                created_at=row["created_at"],
                incident_title=row["message"],
                incident_severity=row["incident_severity"],
                incident_alert_type=row["incident_alert_type"],
                device_id=row["device_id"],
                device_name=row["device_name"],
                device_ip=row["device_ip"],
                is_pending_digest=True,
            ))

    # Sort globally by date desc, then paginate
    results.sort(key=lambda r: r.created_at, reverse=True)
    return results[offset: offset + limit]


@router.post("/test-email", summary="Send a test email to configured recipients")
async def test_email():
    """
    Send a test email to all addresses in NOTIFICATION_EMAILS.
    Returns 200 on delivery, 503 if SMTP is disabled or delivery fails.
    """
    settings = get_settings()

    if not settings.smtp_enabled:
        raise HTTPException(status_code=503, detail="SMTP is disabled (SMTP_ENABLED=false)")

    recipients = settings.notification_email_list
    if not recipients:
        raise HTTPException(status_code=503, detail="No recipients configured (NOTIFICATION_EMAILS is empty)")

    if not settings.smtp_from_email:
        raise HTTPException(status_code=503, detail="SMTP_FROM_EMAIL is not configured")

    if not settings.smtp_username or not settings.smtp_password:
        raise HTTPException(status_code=503, detail="SMTP credentials are missing (SMTP_USERNAME / SMTP_PASSWORD)")

    subject = "[Network Supervisor] Test email — configuration OK"
    body_text = (
        "Ceci est un email de test envoyé par Network Supervisor.\n\n"
        f"SMTP host  : {settings.smtp_host}:{settings.smtp_port}\n"
        f"From       : {settings.smtp_from_email}\n"
        f"Recipients : {', '.join(recipients)}\n\n"
        "Si vous recevez ce message, la configuration SMTP est correcte."
    )
    body_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
  <div style="max-width:600px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:#27ae60;padding:20px 30px;">
      <h2 style="color:#fff;margin:0;">Configuration SMTP — OK</h2>
    </div>
    <div style="padding:30px;">
      <p>Ceci est un email de test envoyé par <strong>Network Supervisor</strong>.</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:6px 0;color:#555;width:120px;">SMTP host</td>
            <td style="padding:6px 0;">{settings.smtp_host}:{settings.smtp_port}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">From</td>
            <td style="padding:6px 0;">{settings.smtp_from_email}</td></tr>
        <tr><td style="padding:6px 0;color:#555;">Destinataires</td>
            <td style="padding:6px 0;">{', '.join(recipients)}</td></tr>
      </table>
      <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
      <p style="color:#888;font-size:12px;margin:0;">
        Si vous recevez ce message, la configuration SMTP est correcte.
      </p>
    </div>
  </div>
</body>
</html>"""

    ok = await send_email(recipients, subject, body_text, body_html)
    if not ok:
        raise HTTPException(status_code=503, detail="Email delivery failed — check logs for SMTP error details")

    logger.info("Test email sent to %s", recipients)
    return {"status": "sent", "recipients": recipients, "smtp_host": settings.smtp_host}
