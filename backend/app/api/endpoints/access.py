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
from app.db.session import get_db

router = APIRouter()

AccessFilter = Literal[
    "all", "active", "blocked_full", "blocked_whatsapp", "bridge", "disconnected",
]


@router.get("/clients")
async def get_access_clients(
    search: str = Query("", description="Match on LR name or IP (case-insensitive)"),
    filter: AccessFilter = Query("all", description="State filter"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Reachable LR clients (stats + filtered, sorted list) — computed in SQL."""
    result = await db.execute(
        text("SELECT fn_access_clients(:search, :filter)"),
        {"search": search, "filter": filter},
    )
    return scalar_json(result)
