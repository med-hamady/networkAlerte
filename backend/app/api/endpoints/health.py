import datetime
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user_or_api_key
from app.core.config import get_settings
from app.db.session import get_db
from app.models.alert import Alert
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Check application and database health."""
    settings = get_settings()
    db_status = "disconnected"

    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        logger.exception("Database health check failed")

    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        app_name=settings.app_name,
        database=db_status,
    )


@router.get("/health/notifications", dependencies=[Depends(require_user_or_api_key)])
async def notifications_health(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Notification delivery health over the last 24h.

    Surfaces the silent-failure mode where the alert engine writes incidents
    to DB but every notification channel rejects them (SMTP misconfigured,
    no policy match…). Point a dashboard / cron at this endpoint and alert
    on `success_rate < 0.95` or `failed > 0`.

    Auth-gated (X-API-Key) since the counts indirectly disclose incident volume.
    """
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)
    res = await db.execute(
        select(Alert.status, func.count())
        .where(Alert.created_at >= cutoff)
        .group_by(Alert.status)
    )
    counts = {row[0]: row[1] for row in res.all()}
    sent = int(counts.get("sent", 0))
    failed = int(counts.get("failed", 0))
    pending = int(counts.get("pending", 0))
    total = sent + failed + pending

    last_failure_q = await db.execute(
        select(Alert.created_at)
        .where(Alert.status == "failed")
        .order_by(Alert.created_at.desc())
        .limit(1)
    )
    last_failure = last_failure_q.scalar_one_or_none()

    last_success_q = await db.execute(
        select(Alert.sent_at)
        .where(Alert.status == "sent")
        .order_by(Alert.sent_at.desc())
        .limit(1)
    )
    last_success = last_success_q.scalar_one_or_none()

    return {
        "window_hours": 24,
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "total": total,
        "success_rate": (sent / total) if total else None,
        "last_success_at": last_success.isoformat() if last_success else None,
        "last_failure_at": last_failure.isoformat() if last_failure else None,
        "status": (
            "ok" if failed == 0 and total > 0 else
            "degraded" if sent > 0 else
            "unknown"
        ),
    }
