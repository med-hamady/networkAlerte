"""Client consumption endpoint — thin wrapper over consumption_service.

The data sourcing (per-LR byte counters) and the site → rocket → client
roll-up live in ``app.services.consumption_service``; this module only wires
the HTTP route. See that service for the counter semantics and performance
notes (matview vs live SQL).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.clients import ClientConsumptionResponse, Period
from app.services import consumption_service

router = APIRouter()


@router.get("/consumption", response_model=ClientConsumptionResponse)
async def get_clients_consumption(
    period: Period = Query("24h", description="24h, 7d, 30d, or lifetime"),
    start: datetime.date | None = Query(
        None, description="Début de plage personnalisée (YYYY-MM-DD, UTC). Requiert `end`."
    ),
    end: datetime.date | None = Query(
        None, description="Fin de plage incluse (YYYY-MM-DD, UTC). Requiert `start`."
    ),
    db: AsyncSession = Depends(get_db),
) -> ClientConsumptionResponse:
    """Cumulative download/upload per client, rolled up site → rocket → client.

    When both ``start`` and ``end`` are provided, the totals are computed over
    that exact date range (inclusive of both days, UTC) and ``period`` is
    ignored. Otherwise the named ``period`` window is used.
    """
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=422,
            detail="`start` et `end` doivent être fournis ensemble.",
        )
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status_code=422,
            detail="`end` doit être postérieure ou égale à `start`.",
        )
    return await consumption_service.get_clients_consumption(
        db, period, start=start, end=end
    )
