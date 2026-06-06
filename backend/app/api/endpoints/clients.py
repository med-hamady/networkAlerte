"""Client consumption endpoint — thin wrapper over consumption_service.

The data sourcing (per-LR byte counters) and the site → rocket → client
roll-up live in ``app.services.consumption_service``; this module only wires
the HTTP route. See that service for the counter semantics and performance
notes (matview vs live SQL).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.clients import ClientConsumptionResponse, Period
from app.services import consumption_service

router = APIRouter()


@router.get("/consumption", response_model=ClientConsumptionResponse)
async def get_clients_consumption(
    period: Period = Query("24h", description="24h, 7d, 30d, or lifetime"),
    db: AsyncSession = Depends(get_db),
) -> ClientConsumptionResponse:
    """Cumulative download/upload per client, rolled up site → rocket → client."""
    return await consumption_service.get_clients_consumption(db, period)
