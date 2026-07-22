"""
Hygiène des IP de LR — retirer les adresses que plus aucune source ne confirme.

Une fiche identifie son client par sa **MAC**, mais tout ce qu'on lui fait passe
par son **IP** : le ping qui dit s'il est en ligne, la session SSH qui lit ses
métriques, et la commande qui coupe ou rétablit son internet. Or les IP sont
distribuées en DHCP — elles bougent. Un client éteint plusieurs jours peut voir
la sienne redonnée à un autre abonné.

Une adresse périmée est donc **pire que pas d'adresse** : on pingue l'équipement
de quelqu'un d'autre (faux « en ligne »), et une coupure demandée par le système
de paiement tombe sur le mauvais client. Une case vide dit « je ne sais pas »,
ce qui est vrai ; une mauvaise adresse dit « je sais », et se trompe.

⚠️ Ce service est le **filet**, pas la protection principale. La protection est
en amont : `ssh_service.identity_refusal` vérifie la MAC de l'équipement joint
avant toute action de blocage, donc même une fiche périmée ne peut pas couper un
innocent. Ici on nettoie l'affichage et on réduit la fenêtre.

Rien n'est perdu : la ligne garde nom, MAC, AP, site et historique, et la
découverte lui rend son IP dès qu'un AP la rapporte.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import PING_FAILURE_STATE_KEY
from app.core.config import get_settings
from app.models.alert_state import AlertState
from app.models.device import Lr
from app.services.discovery_service import is_management_ip

logger = logging.getLogger(__name__)


def _aware(value: datetime.datetime | None) -> datetime.datetime | None:
    """Une colonne timestamptz peut remonter naïve selon le driver.

    Comparer naïf et aware lèverait un TypeError à mi-parcours d'un nettoyage.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value


def is_confirmed(
    ip: str | None,
    uisp_status: str | None,
    uisp_last_seen: datetime.datetime | None,
    last_discovered_at: datetime.datetime | None,
    since: datetime.datetime,
    trust_hours: int,
) -> bool:
    """Une source confirme-t-elle ENCORE l'IP portée par cette ligne ?

    Préalable : une IP **hors plan de management** (LAN d'usine `192.168.x`,
    APIPA…) n'est JAMAIS confirmable, quelle que soit la fraîcheur de la source.
    Elle est fausse par construction — nous ne joignons rien par là.

    Trois façons de confirmer une IP du plan :
      * le radio a redécouvert la station après `since` — lui voit le terrain ;
      * UISP voit la station **en ligne** — l'adresse est celle de maintenant ;
      * UISP l'a vue il y a moins de `trust_hours` — le bail DHCP n'a quasi
        sûrement pas bougé.

    Ce dernier point est une **fenêtre**, pas un booléen `active` : une station
    en panne depuis 1 h et une disparue depuis 3 semaines portent toutes deux
    `disconnected`, mais leur dernière IP connue n'a pas la même valeur.
    """
    if not is_management_ip(ip):
        return False
    if (uisp_status or "").lower() == "active":
        return True
    last_discovered_at = _aware(last_discovered_at)
    if last_discovered_at is not None and last_discovered_at > since:
        return True
    uisp_last_seen = _aware(uisp_last_seen)
    if uisp_last_seen is None:
        return False
    return uisp_last_seen > datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        hours=trust_hours
    )


def plan_cleanup(
    rows: list[Lr], since: datetime.datetime, trust_hours: int,
) -> tuple[list[Lr], list[tuple[Lr, str | None]]]:
    """Décide, sans rien modifier — renvoie (gardées, [(ligne, IP à retirer)]).

    Décider et muter dans la même boucle avait un défaut discret : l'IP était
    effacée AVANT d'être affichée, donc le formatage tombait sur un `None` et
    tuait le traitement au milieu des mutations. Séparer la décision de
    l'écriture rend ça structurellement impossible, et rend la décision
    testable sans base.
    """
    kept: list[Lr] = []
    cleared: list[tuple[Lr, str | None]] = []
    for lr in rows:
        if is_confirmed(
            lr.ip_address, lr.uisp_status, lr.uisp_last_seen,
            lr.last_discovered_at, since, trust_hours,
        ):
            kept.append(lr)
        else:
            cleared.append((lr, lr.ip_address))
    return kept, cleared


async def run_cleanup(
    session: AsyncSession,
    *,
    apply: bool,
    since: datetime.datetime | None = None,
    trust_hours: int | None = None,
    radio_hours: int | None = None,
    ips: set[str] | None = None,
) -> dict:
    """Applique (ou simule) le nettoyage. L'appelant commite.

    `ips` restreint le périmètre ; sans lui, toutes les lignes portant une IP
    sont examinées. `since` borne la confirmation par le radio ; sans lui, on
    prend « vu depuis moins de `radio_hours` ».
    """
    settings = get_settings()
    trust_hours = trust_hours if trust_hours is not None else settings.uisp_ip_trust_hours
    radio_hours = radio_hours if radio_hours is not None else settings.ip_cleanup_radio_hours
    if since is None:
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=radio_hours)

    query = select(Lr).where(Lr.ip_address.is_not(None))
    if ips:
        query = query.where(Lr.ip_address.in_(ips))
    rows = (await session.execute(query)).scalars().all()

    kept, cleared = plan_cleanup(list(rows), since, trust_hours)

    if apply:
        for lr, _old_ip in cleared:
            lr.ip_address = None
            # Sans IP, la ligne sort du sweep de ping : plus rien ne mesure son
            # état. « unknown » est l'état honnête — le laisser sur sa dernière
            # valeur la ferait mentir indéfiniment.
            lr.status = "unknown"
            # Compteur d'échecs de ping purgé avec l'IP : sinon la station
            # rebasculerait « down » au premier paquet perdu après son retour.
            await session.execute(
                delete(AlertState).where(
                    AlertState.device_id == lr.id,
                    AlertState.alert_type == PING_FAILURE_STATE_KEY,
                )
            )

    return {
        "examined": len(rows),
        "kept": len(kept),
        "cleared": len(cleared),
        "applied": apply,
        "samples": [
            {"name": lr.name, "ip": old_ip} for lr, old_ip in cleared[:10]
        ],
    }
