from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.client_signal import ClientSignalResponse
from app.services import client_signal_service

router = APIRouter()


@router.get("", response_model=ClientSignalResponse)
async def get_client_signal(
    mac: str = Query(
        ...,
        description="MAC du LR client (formats acceptés : aa:bb:cc:dd:ee:ff, "
        "aa-bb-..., aabb.ccdd.eeff, aabbccddeeff)",
    ),
    db: AsyncSession = Depends(get_db),
) -> ClientSignalResponse:
    """Qualité actuelle du signal d'un client, par MAC de son LR.

    Destiné à un système tiers : passe le ``mac`` du LR, reçoit une catégorie
    qualitative (``excellent`` / ``bien`` / ``moyen`` / ``faible``) calculée à
    partir de la dernière valeur de signal connue en base. ``quality`` vaut
    ``indetermine`` si le LR existe mais n'a aucune mesure de signal récente.

    - 400 si le MAC est mal formé.
    - 404 si aucun LR ne porte ce MAC.
    """
    try:
        result = await client_signal_service.get_client_signal(db, mac)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"Aucun LR avec le MAC {mac!r}")
    return result
