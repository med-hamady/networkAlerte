"""Diagnostics d'accès aux LR — endpoint de lecture pour le dashboard.

Deux anomalies de gestion du parc abonné, agrégées sur une page dédiée :
LR qui refusent le SSH, et LR vus par le radio mais absents de UISP. Toute la
logique est dans `access_diagnostics_service`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import access_diagnostics_service

router = APIRouter()


@router.get("")
async def get_access_diagnostics(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """LR refusant le SSH + LR découverts par radio mais absents de UISP."""
    return await access_diagnostics_service.get_access_diagnostics(db)
