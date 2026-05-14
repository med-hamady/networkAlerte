from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.lr_health import BadInstallationsResponse
from app.services import lr_health_service

router = APIRouter()


@router.get("/bad-installations", response_model=BadInstallationsResponse)
async def list_bad_installations(
    days: int = Query(30, ge=1, le=365, description="Sliding window in days"),
    db: AsyncSession = Depends(get_db),
) -> BadInstallationsResponse:
    """LR clients with recurring bad-link incidents, worst first.

    Surfaces installations suspected to be poorly executed: signal_low, ccq_low,
    cinr_low, radio_link_degraded, capacity_low, ccq_ul_low, cinr_ul_low,
    capacity_ul_low, high_rx_tx_errors, lr_no_transit on the given window.
    """
    return await lr_health_service.get_bad_installations(db, days=days)
