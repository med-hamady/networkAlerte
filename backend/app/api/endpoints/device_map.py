"""Client map endpoint — thin wrapper over device_map_service.

The split between plottable points and bad-data outliers lives in
``app.services.device_map_service``; this module only wires the HTTP route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import device_map_service

router = APIRouter()


@router.get("")
async def get_client_map(db: AsyncSession = Depends(get_db)) -> dict:
    """Client positions for the map, plus the outliers to fix in the field.

    ``points`` are inside Mauritania and safe to plot; ``outliers`` carry a
    ``reason`` and exist so the bad provisioning is visible instead of silently
    dropped. ``stats`` reports coverage (how many LRs have no position at all).
    """
    return await device_map_service.get_client_map(db)
