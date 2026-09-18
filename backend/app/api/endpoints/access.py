"""Client-access table — thin wrapper over fn_access_clients(search, filter).

Stats, search, filter and sort run in SQL; the frontend renders `items` and
`stats` directly.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import caller_has_permission
from app.api.rpc import scalar_json
from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter()

AccessFilter = Literal[
    "all", "active", "blocked_full", "blocked_whatsapp", "bridge", "disconnected",
    "out_of_supervision", "out_of_supervision_30d", "out_of_supervision_90d",
    "blocked", "blocked_ssh", "blocked_router", "blocked_pending",
]


@router.get("/clients")
async def get_access_clients(
    request: Request,
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
    payload = scalar_json(result)

    # ⚠️ Les compteurs sont RETIRÉS DE LA RÉPONSE, pas seulement cachés à
    # l'écran. Ils voyagent dans la même réponse que la liste : les masquer
    # côté navigateur les laisserait parfaitement lisibles dans l'onglet
    # réseau, donc le droit ne vaudrait rien.
    #
    # ⚠️ `stats` est mis à **None**, jamais à un objet de zéros : un profil
    # sans ce droit verrait alors « 0 client » — un chiffre FAUX, pire que pas
    # de chiffre du tout (le frontend afficherait en plus sa bannière « parc
    # vide »). L'absence se distingue, un zéro non.
    #
    # Le filtrage et le tri, eux, restent entiers : ce droit porte sur la
    # TAILLE DU PARC, pas sur la capacité à traiter un abonné.
    if not caller_has_permission(request, "fai.stats"):
        payload["stats"] = None
    return payload
