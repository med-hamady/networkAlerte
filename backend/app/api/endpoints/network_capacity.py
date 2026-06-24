"""Network capacity endpoint — thin wrapper over network_capacity_service.

The roll-up (per-family + per-site consumed vs available client slots, plus the
per-Rocket drill-down) lives in ``app.services.network_capacity_service``; this
module only wires the HTTP route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import network_capacity_service, site_infra_service

router = APIRouter()


@router.get("")
async def get_network_capacity(db: AsyncSession = Depends(get_db)) -> dict:
    """Client-capacity overview: LTU/airMAX donuts + per-site breakdown.

    Also carries the per-site **infra-equipment budget** roll-up under ``infra``
    (count of Rockets/AF60/PTP per site vs ``SITE_INFRA_MAX``), so the /capacity
    page can render it without a second request.
    """
    capacity = await network_capacity_service.get_network_capacity(db)
    capacity["infra"] = await site_infra_service.get_site_infra_capacity(db)
    return capacity
