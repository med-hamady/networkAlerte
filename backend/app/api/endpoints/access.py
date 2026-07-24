"""Client-access table — thin wrapper over fn_access_clients(search, filter).

Stats, search, filter and sort run in SQL; the frontend renders `items` and
`stats` directly.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rpc import scalar_json
from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter()

AccessFilter = Literal[
    "all", "active", "blocked_full", "blocked_whatsapp", "bridge", "disconnected",
    "out_of_supervision", "out_of_supervision_30d", "out_of_supervision_90d",
]


@router.get("/clients")
async def get_access_clients(
    search: str = Query("", description="Match on LR name or IP (case-insensitive)"),
    filter: AccessFilter = Query("all", description="State filter"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Reachable LR clients (stats + filtered, sorted list) — computed in SQL."""
    result = await db.execute(
        # Le seuil « hors supervision » est passé depuis la config plutôt que
        # gravé dans la fonction : l'opérateur l'ajuste dans le `.env`, sans
        # migration (cf. `Settings.out_of_supervision_days`).
        text("SELECT fn_access_clients(:search, :filter, :out_of_supervision_days)"),
        {
            "search": search,
            "filter": filter,
            "out_of_supervision_days": get_settings().out_of_supervision_days,
        },
    )
    return scalar_json(result)
