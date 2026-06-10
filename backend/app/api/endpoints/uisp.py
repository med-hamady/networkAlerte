"""UISP controller sync — on-demand trigger / preview.

POST /api/v1/uisp/sync            → run the sync (create/update infra devices)
POST /api/v1/uisp/sync?dry_run=true → preview only, writes nothing

The periodic job (uisp_sync_job) does the same thing on UISP_SYNC_INTERVAL.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services import uisp_service, uisp_sync_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sync")
async def trigger_uisp_sync(
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Import infrastructure devices from the UISP controller.

    Returns a summary (counts + sample create/update list). With dry_run=true,
    computes what would change without writing anything.
    """
    settings = get_settings()
    has_auth = settings.uisp_api_token or (settings.uisp_username and settings.uisp_password)
    if not settings.uisp_base_url or not has_auth:
        raise HTTPException(
            status_code=400,
            detail="UISP sync not configured — set UISP_BASE_URL and UISP_API_TOKEN "
            "(or UISP_USERNAME/UISP_PASSWORD) in the environment.",
        )
    try:
        summary = await uisp_sync_service.sync_uisp_devices(db, dry_run=dry_run)
    except uisp_service.UISPAuthError as exc:
        raise HTTPException(status_code=502, detail=f"UISP authentication failed: {exc}") from exc
    except Exception as exc:
        logger.error("UISP sync request failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"UISP sync failed: {exc}") from exc

    if not dry_run:
        await db.commit()
    return summary
