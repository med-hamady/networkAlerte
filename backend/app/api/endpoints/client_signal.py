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
    """Qualité actuelle du signal **et de la latence** d'un client, par MAC de son LR.

    Destiné à un système tiers : passe le ``mac`` du LR, reçois

    - ``quality`` : catégorie du **signal** (``excellent`` / ``bien`` / ``moyen``
      / ``faible``), lue de la dernière valeur connue en base ;
    - ``latency_quality`` + ``latency_message`` : la **latence mesurée EN DIRECT
      à cet appel** — le LR ping ``lr_latency_target`` avec 5 paquets de 56 o —
      classée ``excellent`` (< 80 ms) / ``tres_bien`` (80-100) / ``bien``
      (100-120) / ``mauvaise`` (120-150) / ``catastrophique`` (≥ 150).

    Chaque catégorie vaut ``indetermine`` quand la donnée manque (pas de mesure
    de signal récente ; LR injoignable ou sans transit pour la latence) —
    ``latency_message`` en donne alors la raison.

    ⚠️ **Appel lent** : il ouvre une session SSH sur le LR, comptez ~6-15 s (plus
    sur un lien radio dégradé). Prévoir le timeout client en conséquence.

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
