"""Traffic endpoint — thin wrapper over traffic_service.

Exposes the top client→Internet destinations by operator/CDN (ASN), built from
the NetFlow aggregates in ``traffic_dest_stats``. The roll-up lives in
``app.services.traffic_service``; this module only wires the HTTP route.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import traffic_service

router = APIRouter()


@router.get("/top-destinations")
async def get_top_destinations(
    period: Literal["24h", "7d", "30d"] = Query("24h"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Top operators/CDNs our clients consult, by traffic volume over ``period``."""
    return await traffic_service.get_top_destinations(db, period)
