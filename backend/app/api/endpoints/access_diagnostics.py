"""Diagnostics d'accès aux LR — endpoint de lecture pour le dashboard.

Deux anomalies de gestion du parc abonné, agrégées sur une page dédiée :
LR qui refusent le SSH, et LR vus par le radio mais absents de UISP. Toute la
logique est dans `access_diagnostics_service`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import access_diagnostics_service, uisp_enrollment_service

router = APIRouter()


@router.get("")
async def get_access_diagnostics(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """LR refusant le SSH + LR découverts par radio mais absents de UISP."""
    return await access_diagnostics_service.get_access_diagnostics(db)


class EnrollUispRequest(BaseModel):
    # Sélection de LR à enrôler. Omis/vide = toute la population de l'anomalie
    # « vu par radio, absent de UISP » — c'est-à-dire exactement ce que la page
    # affiche.
    lr_ids: list[int] = []
    # Écraser la clé même quand l'équipement pointe déjà sur ce contrôleur.
    # Sur un équipement sain, cela le DÉ-enrôle — action explicite seulement.
    force: bool = False


@router.post("/enroll-uisp")
async def enroll_uisp_bulk(
    body: EnrollUispRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Enrôle en lot les LR vus par le radio mais absents de UISP.

    Pose la clé du contrôleur par SSH sur chaque équipement et attend son
    adoption. Opération de fond : chaque LR peut prendre jusqu'à 45 s (le temps
    de la poignée de main avec le contrôleur) et la concurrence SSH est bornée
    pour ne pas saturer les radios — une régularisation de tout le parc se
    compte en minutes, pas en secondes.
    """
    if not uisp_enrollment_service.enrollment_available():
        raise HTTPException(
            status_code=409,
            detail=(
                "Aucune clé UISP configurée. Renseigner UISP_DEVICE_KEY dans le "
                ".env (UISP → Paramètres → Équipements → clé UISP) puis relancer."
            ),
        )
    return await uisp_enrollment_service.enroll_many(
        db,
        body.lr_ids if body else None,
        force=bool(body and body.force),
    )
