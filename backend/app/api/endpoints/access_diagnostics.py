"""Diagnostics d'accès aux LR — endpoint de lecture pour le dashboard.

Deux anomalies de gestion du parc abonné, agrégées sur une page dédiée :
LR qui refusent le SSH, et LR vus par le radio mais absents de UISP. Toute la
logique est dans `access_diagnostics_service`.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.db.session import get_db
from app.services import (
    access_diagnostics_service,
    uisp_assignment_service,
    uisp_enrollment_service,
    uisp_service,
)

router = APIRouter()


@router.get("",
    dependencies=[Depends(require_permission("access_diagnostics.view"))],
)
async def get_access_diagnostics(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """LR refusant le SSH + LR découverts par radio mais absents de UISP."""
    return await access_diagnostics_service.get_access_diagnostics(db)


@router.get("/crm-clients",
    dependencies=[Depends(require_permission("uisp.assign"))],
)
async def search_crm_clients(
    q: str = Query(..., min_length=1, max_length=100),
) -> dict[str, Any]:
    """Clients CRM par nom ou id — la liste où l'opérateur choisit à qui
    rattacher un équipement (colonne « Action » de la page).

    ⚠️ Ici et PAS dans `uisp_assign.py` : ce router porte la clé cloisonnée du
    système de paiement, et une dépendance de router étant additive, toute
    route ajoutée là-bas s'ouvrirait à cette clé en silence. Même droit que le
    rattachement lui-même (`uisp.assign`) : chercher un client ne sert qu'à ça.
    """
    if not uisp_assignment_service.is_configured():
        raise HTTPException(status_code=409, detail="UISP non configuré (UISP_BASE_URL + token).")
    try:
        clients = await uisp_assignment_service.search_crm_clients(q)
    except (uisp_service.UISPAuthError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Contrôleur UISP injoignable : {exc}") from exc
    return {"clients": clients}


class EnrollUispRequest(BaseModel):
    # Sélection de LR à enrôler. Omis/vide = toute la population de l'anomalie
    # « vu par radio, absent de UISP » — c'est-à-dire exactement ce que la page
    # affiche.
    lr_ids: list[int] = []
    # Écraser la clé même quand l'équipement pointe déjà sur ce contrôleur.
    # Sur un équipement sain, cela le DÉ-enrôle — action explicite seulement.
    force: bool = False


@router.post("/enroll-uisp",
    dependencies=[Depends(require_permission("access_diagnostics.enroll"))],
)
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
