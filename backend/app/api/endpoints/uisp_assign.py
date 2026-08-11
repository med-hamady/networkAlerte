"""UISP — association d'un équipement (MAC) à un client CRM, pour un tiers.

Route séparée de `uisp.py` (qui porte `/uisp/sync`) pour une raison
d'**autorisation**, exactement comme `fai_verify.py` l'est de `fai.py` : elle est
appelée par le système de paiement, qui adopte les équipements nouvellement
installés. Il tient sa propre clé (`uisp_assign_api_key`, dépendance
`require_uisp_assign_client`), scellée à cette seule route.

Une dépendance au niveau du router est additive et ne peut pas être surchargée
par route : sortir `/assign` dans son propre router est donc la seule façon de
lui donner une auth propre sans ouvrir `/uisp/sync` — qui, lui, écrit dans
l'inventaire et doit rester sous la clé maîtresse.

⚠️ **Pourquoi ce cloisonnement n'est pas cosmétique** : cette route est servie
sur la VIP publique `.229`, qui donne accès à l'API ENTIÈRE (contrairement au
listener `.233`, restreint à `/fai`). Sans clé dédiée, faire consommer
`/uisp/assign` par un tiers revient à lui confier `api_key`, donc
`DELETE /devices/{id}` et `/uisp/sync` par la même occasion.

⚠️ **La route est LENTE par nature** : si l'équipement est absent du contrôleur,
sa clé UISP lui est posée **par SSH** avant l'association. Le proxy doit lui
laisser le temps correspondant (cf. la `location` dédiée dans nginx.conf) — un
timeout court rendrait une erreur sur une adoption réussie.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import uisp_assignment_service, uisp_service

logger = logging.getLogger(__name__)
router = APIRouter()


class AssignRequest(BaseModel):
    """Le contrat, minimal : l'équipement et le client (+ le service si besoin)."""

    mac: str = Field(..., description="MAC de l'équipement (toute notation acceptée)")
    crm_client_id: str = Field(..., description="Id du client dans le CRM")
    # Nécessaire uniquement pour les clients à plusieurs services (6 sur 1402) :
    # c'est le seul moyen de désigner lequel, leurs noms étant souvent
    # identiques. Sans lui, un tel client renvoie 409 avec ses services.
    crm_service_id: str | None = Field(
        None, description="Id du service CRM — requis si le client en a plusieurs",
    )
    # Déplacer un équipement DÉJÀ rattaché à un autre client lui retire son
    # matériel. Refusé par défaut : une MAC saisie de travers ferait ce dégât
    # en silence. Passer true seulement si le déplacement est bien l'intention.
    reassign: bool = False


@router.post("/assign")
async def assign_to_crm_client(
    body: AssignRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Associe un équipement à un client CRM — l'équivalent du formulaire UISP.

    Transposition du geste manuel : chercher la MAC, la voir en « unknown »,
    cliquer dessus et choisir le client. Si l'équipement est absent du
    contrôleur, sa clé lui est posée d'abord (sans elle il ne se déclare jamais)
    et la réponse porte `pending_registration` — l'appel se rejoue dans la minute.

    Codes : 400 MAC mal formée · 404 client CRM introuvable, ou service
    n'appartenant pas à ce client · 409 le client a plusieurs services et
    `crm_service_id` n'est pas fourni (les services sont renvoyés) · 403 token
    UISP sans droits d'écriture.
    """
    if not uisp_assignment_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="UISP non configuré — renseigner UISP_BASE_URL et UISP_API_TOKEN "
            "(ou UISP_USERNAME/UISP_PASSWORD) dans l'environnement.",
        )
    try:
        return await uisp_assignment_service.assign_device_to_crm_client(
            db, body.mac, body.crm_client_id, body.crm_service_id, body.reassign,
        )
    except ValueError as exc:  # MAC mal formée
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except uisp_assignment_service.AmbiguousClientError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "candidates": exc.candidates},
        ) from exc
    except uisp_assignment_service.AlreadyAssignedError as exc:
        # 409 et non 404 : rien n'est « introuvable », c'est un conflit d'état
        # que l'appelant peut lever en connaissance de cause (reassign=true).
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "current_crm_client_id": exc.current_crm_client_id,
            },
        ) from exc
    except uisp_assignment_service.DeviceUnreachableError as exc:
        # Échec technique sur l'équipement — surtout pas un 404, qui enverrait
        # l'appelant vérifier ses identifiants au lieu de l'équipement.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except uisp_assignment_service.AssignmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except uisp_service.UISPAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("UISP assign failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Association UISP échouée : {exc}") from exc
