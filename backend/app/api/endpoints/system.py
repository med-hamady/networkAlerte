import asyncio
import logging
import platform
import socket
import subprocess
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services import threshold_service, whatsapp_service
from app.services.email_service import send_email

logger = logging.getLogger(__name__)
router = APIRouter()


class GpuInfo(BaseModel):
    name: str
    memory_total_mb: int | None = None
    memory_used_mb: int | None = None
    temperature_c: int | None = None
    utilization_pct: int | None = None


class SystemInfo(BaseModel):
    hostname: str
    os_name: str
    cpu_count: int
    cpu_percent: float
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    disk_total_gb: float
    disk_used_gb: float
    disk_percent: float
    gpus: list[GpuInfo]


def _query_nvidia_smi() -> list[GpuInfo]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            def _int(v: str) -> int | None:
                try:
                    return int(v)
                except ValueError:
                    return None
            gpus.append(GpuInfo(
                name=parts[0],
                memory_total_mb=_int(parts[1]),
                memory_used_mb=_int(parts[2]),
                temperature_c=_int(parts[3]),
                utilization_pct=_int(parts[4]),
            ))
        return gpus
    except Exception:
        return []


@router.get("/info", response_model=SystemInfo)
async def get_system_info() -> SystemInfo:
    cpu_percent, gpus = await asyncio.gather(
        asyncio.to_thread(psutil.cpu_percent, 0.2),
        asyncio.to_thread(_query_nvidia_smi),
    )

    ram  = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("/")
    except Exception:
        disk = psutil.disk_usage("C:\\")

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=platform.system(),
        cpu_count=psutil.cpu_count(logical=True) or 1,
        cpu_percent=round(cpu_percent, 1),
        ram_total_gb=round(ram.total / 1024**3, 1),
        ram_used_gb=round(ram.used  / 1024**3, 1),
        ram_percent=round(ram.percent, 1),
        disk_total_gb=round(disk.total / 1024**3, 1),
        disk_used_gb=round(disk.used  / 1024**3, 1),
        disk_percent=round(disk.percent, 1),
        gpus=gpus,
    )


# ---------------------------------------------------------------------------
# Alert thresholds — GET / PATCH / DELETE per key
# ---------------------------------------------------------------------------

@router.get("/thresholds")
async def get_thresholds(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return all configurable alert thresholds with their current effective values."""
    return await threshold_service.get_all_thresholds(db, get_settings())


@router.patch("/thresholds")
async def patch_thresholds(
    updates: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Update one or more threshold values. Unknown keys are silently ignored."""
    if not updates:
        raise HTTPException(status_code=422, detail="No values provided")
    await threshold_service.set_thresholds(db, updates)
    await db.commit()
    return await threshold_service.get_all_thresholds(db, get_settings())


@router.delete("/thresholds/{key}", status_code=204)
async def reset_threshold(
    key: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove the DB override for a threshold key, reverting to the env default."""
    deleted = await threshold_service.reset_threshold(db, key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No override found for key '{key}'")
    await db.commit()


# ---------------------------------------------------------------------------
# SMTP diagnostic — send a test email to the configured recipients
# ---------------------------------------------------------------------------

@router.post("/test-email", summary="Send a test email to configured recipients")
async def test_email() -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# WhatsApp diagnostic — send a test message to the configured group (Ultramsg)
# ---------------------------------------------------------------------------

@router.post("/test-whatsapp", summary="Send a test WhatsApp message to the configured group")
async def test_whatsapp() -> dict[str, Any]:
    """
    Send a test message to the configured WhatsApp group via Ultramsg.
    Returns 200 on delivery, 503 if WhatsApp is disabled/misconfigured or the
    send fails.
    """
    settings = get_settings()

    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=503, detail="WhatsApp is disabled (WHATSAPP_ENABLED=false)")
    if not settings.whatsapp_instance_id or not settings.whatsapp_token:
        raise HTTPException(status_code=503, detail="WhatsApp credentials missing (WHATSAPP_INSTANCE_ID / WHATSAPP_TOKEN)")
    if not settings.whatsapp_group_id:
        raise HTTPException(status_code=503, detail="WhatsApp group not configured (WHATSAPP_GROUP_ID is empty)")

    body = (
        "*[Network Supervisor] Test WhatsApp — configuration OK*\n"
        f"Instance : {settings.whatsapp_instance_id}\n"
        f"Groupe   : {settings.whatsapp_group_id}\n\n"
        "Si vous recevez ce message, la configuration Ultramsg est correcte."
    )
    ok = await whatsapp_service.send_whatsapp(body)
    if not ok:
        raise HTTPException(status_code=503, detail="WhatsApp delivery failed — check logs for Ultramsg error details")

    logger.info("Test WhatsApp sent to group %s", settings.whatsapp_group_id)
    return {"status": "sent", "group": settings.whatsapp_group_id, "instance": settings.whatsapp_instance_id}
