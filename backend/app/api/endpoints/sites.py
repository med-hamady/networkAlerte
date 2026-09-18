"""Per-site overview cards — thin wrapper over fn_site_overview().

Grouping devices by site, counting infra / online / blocked clients, finding the
oldest down_since, and assembling the per-site down-device and power-device lists
all happen in SQL. The frontend only renders the returned array.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import caller_has_permission
from app.api.rpc import scalar_json
from app.db.session import get_db

router = APIRouter()


# Les compteurs d'ABONNÉS d'une carte de site. Retirés ensemble : séparer « en
# ligne » de « bloqués » n'aurait pas de sens, les deux disent la même chose sur
# la taille du parc du site.
_CLIENT_COUNT_KEYS = ("clients_online", "clients_blocked")


@router.get("/overview")
async def get_sites_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Site cards (counts + down/power device lists), sorted by name in SQL."""
    result = await db.execute(text("SELECT fn_site_overview()"))
    cards = scalar_json(result)

    # ⚠️ Retrait CÔTÉ SERVEUR, et pas seulement à l'écran — contrairement à
    # `dashboard.stats`, qui est un masquage d'affichage assumé.
    #
    # La raison est précise : sommés sur les sites, ces deux compteurs
    # RECONSTITUENT exactement ce que `fai.stats` retire de `/access`
    # (total d'abonnés, part bloquée). Les laisser partir au navigateur ouvrirait
    # donc une porte dérobée sur le chiffre qu'on vient de fermer à côté — et le
    # droit `fai.stats` ne vaudrait plus rien.
    #
    # Mis à **None**, jamais à 0 : « 0 client en ligne » sur un site qui en porte
    # 128 est un chiffre FAUX, et il se lirait comme une panne totale du site.
    # « Équipements infra » et « Pannes » restent entiers : un profil de
    # supervision en a besoin pour travailler.
    if not caller_has_permission(request, "sites.client_counts"):
        for card in cards:
            for key in _CLIENT_COUNT_KEYS:
                if key in card:
                    card[key] = None
    return cards
