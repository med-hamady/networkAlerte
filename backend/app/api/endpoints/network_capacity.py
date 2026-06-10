"""Network capacity endpoint — thin wrapper over network_capacity_service.

The roll-up (per-family + per-site consumed vs available client slots, plus the
per-Rocket drill-down) lives in ``app.services.network_capacity_service``; this
module only wires the HTTP route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import network_capacity_service

router = APIRouter()


@router.get("")
async def get_network_capacity(db: AsyncSession = Depends(get_db)) -> dict:
    """Client-capacity overview: LTU/airMAX donuts + per-site breakdown."""
    return await network_capacity_service.get_network_capacity(db)
