"""Diagnostics d'accès aux LR — deux anomalies que rien d'autre ne surface.

La page « Diagnostics d'accès » agrège deux problèmes distincts de gestion du
parc abonné, tous deux invisibles ailleurs dans le dashboard :

1. **Les LR qui REFUSENT le SSH** — mot de passe invalide, SSH désactivé sur
   l'équipement, ou clé d'hôte incompatible. C'est un défaut de gestion (on ne
   peut plus piloter le LR : ni bloquer, ni sonder, ni corriger), pas une simple
   panne. La donnée vient de `lrs.ssh_status`, renseigné à chaque tour de
   `lr_internet_probe_job` (voir `ssh_service.classify_probe_ssh_status`). On ne
   remonte QUE les LR encore `up` : un LR down n'est pas en train de « refuser »,
   il est juste éteint (couvert par device_ping_job), et la sonde ne le teste
   même pas — son `ssh_status` serait périmé.

2. **Découverts par radio mais absents de UISP** — un LR que la découverte radio
   a vu (`last_discovered_at` renseigné) alors que sa MAC n'est jamais apparue
   dans le roster des stations UISP (`uisp_synced_at` NULL). Autrement dit : un
   client physiquement branché et actif sur une antenne, mais **non provisionné
   dans UISP** — donc potentiellement non facturé, ou oublié à l'inventaire. Le
   sync UISP ne le supprime jamais (il n'appartient qu'à discovery_service), d'où
   l'intérêt de le signaler ici pour régularisation manuelle.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Lr
from app.services import ssh_service


def _iso(dt: datetime.datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def get_ssh_refusing_lrs(session: AsyncSession) -> list[dict[str, Any]]:
    """LR encore `up` dont le SSH est refusé (auth / désactivé / clé d'hôte).

    Trié par ancienneté du dernier contrôle décroissante n'aurait pas de sens
    (tous fraîchement sondés) ; on trie par catégorie puis nom pour un affichage
    stable, les cas « mot de passe » (les plus courants) groupés.
    """
    rows = (
        await session.execute(
            select(Lr).where(
                Lr.ssh_status.in_(ssh_service.SSH_REFUSAL_STATUSES),
                Lr.status == "up",
            )
        )
    ).scalars().all()

    out = [
        {
            "id": lr.id,
            "name": lr.name,
            "mac": lr.mac_address,
            "ip_address": lr.ip_address,
            "site": lr.site,
            "ap_name": lr.rocket.name if lr.rocket else lr.uisp_ap_name,
            "ssh_status": lr.ssh_status,
            "ssh_error": lr.ssh_error,
            "ssh_checked_at": _iso(lr.ssh_checked_at),
            # Contexte utile : un LR qu'on veut couper mais qu'on ne peut plus
            # joindre est un cas prioritaire (le blocage ne s'appliquera pas).
            "client_blocked": lr.client_blocked,
        }
        for lr in rows
    ]
    out.sort(key=lambda r: (r["ssh_status"], (r["name"] or "").casefold()))
    return out


async def get_radio_only_not_in_uisp(session: AsyncSession) -> list[dict[str, Any]]:
    """LR vus par le radio mais jamais dans le roster UISP (non provisionnés).

    `last_discovered_at IS NOT NULL` = la découverte radio l'a rattaché à un AP au
    moins une fois ; `uisp_synced_at IS NULL` = le sync des stations UISP ne l'a
    jamais ni créé ni matché. L'intersection = un client réel absent de UISP.
    """
    rows = (
        await session.execute(
            select(Lr).where(
                Lr.last_discovered_at.is_not(None),
                Lr.uisp_synced_at.is_(None),
            )
        )
    ).scalars().all()

    out = [
        {
            "id": lr.id,
            "name": lr.name,
            "mac": lr.mac_address,
            "ip_address": lr.ip_address,
            "site": lr.site,
            "ap_name": lr.rocket.name if lr.rocket else None,
            "status": lr.status,
            "last_discovered_at": _iso(lr.last_discovered_at),
        }
        for lr in rows
    ]
    out.sort(key=lambda r: ((r["site"] or "").casefold(), (r["name"] or "").casefold()))
    return out


async def get_access_diagnostics(session: AsyncSession) -> dict[str, Any]:
    """Les deux listes + leurs compteurs, pour la page « Diagnostics d'accès »."""
    ssh_refused = await get_ssh_refusing_lrs(session)
    radio_not_in_uisp = await get_radio_only_not_in_uisp(session)
    return {
        "ssh_refused": ssh_refused,
        "radio_not_in_uisp": radio_not_in_uisp,
        "counts": {
            "ssh_refused": len(ssh_refused),
            "radio_not_in_uisp": len(radio_not_in_uisp),
        },
    }
