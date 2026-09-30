"""Network capacity endpoint — thin wrapper over network_capacity_service.

The roll-up (per-family + per-site consumed vs available client slots, plus the
per-Rocket drill-down) lives in ``app.services.network_capacity_service``; this
module only wires the HTTP route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import caller_has_permission
from app.db.session import get_db
from app.services import network_capacity_service, site_infra_service

router = APIRouter()


def _strip_totals(capacity: dict) -> dict:
    """Retirer les CHIFFRES DE CAPACITÉ, en gardant les Rockets saturés.

    Ce que voit un profil sans `capacity.totals` : les Rockets saturés, et le
    budget d'équipements infra par site. Disparaissent : les deux cercles
    LTU/airMAX et la section « Capacité par site ».

    ⚠️ **On ne peut PAS simplement retirer `sites`.** La liste « Rockets
    saturés » est construite par le NAVIGATEUR en parcourant
    `sites[].rockets[]` : vider `sites` viderait aussi la seule section que
    l'opérateur veut garder. Le retrait est donc chirurgical —

      - `families` → `None` : plus de cercles globaux ;
      - les agrégats `ltu`/`airmax` de chaque site → `None` : la section
        « Capacité par site » n'a plus de quoi tracer ses barres ;
      - `rockets[]` réduit aux **saturés** : la liste reste exacte, et le
        payload cesse de porter le compte de clients de CHAQUE Rocket — sinon
        leur somme redonnerait le total qu'on vient de retirer, à un `reduce`
        près dans la console du navigateur.

    ⚠️ Les sites sont TOUS conservés (même sans Rocket saturé) : la section
    infra s'en sert pour savoir vers lesquels on peut naviguer, et un site
    absent s'y afficherait comme non cliquable sans raison.

    ⚠️ `None` et jamais 0 : « 0 client sur 0 » se lirait comme un réseau vide.
    """
    capacity["families"] = None
    for site in capacity.get("sites") or []:
        site["ltu"] = None
        site["airmax"] = None
        site["unknown"] = None
        site["rockets"] = [
            rocket
            for rocket in site.get("rockets") or []
            if (rocket.get("max_clients") or 0) > 0
            and rocket.get("current_clients", 0) >= rocket["max_clients"]
        ]
    return capacity


@router.get("")
async def get_network_capacity(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Client-capacity overview: LTU/airMAX donuts + per-site breakdown.

    Also carries the per-site **infra-equipment budget** roll-up under ``infra``
    (count of Rockets/AF60/PTP per site vs ``SITE_INFRA_MAX``), so the /capacity
    page can render it without a second request.
    """
    capacity = await network_capacity_service.get_network_capacity(db)
    capacity["infra"] = await site_infra_service.get_site_infra_capacity(db)
    if not caller_has_permission(request, "capacity.totals"):
        capacity = _strip_totals(capacity)
    return capacity
