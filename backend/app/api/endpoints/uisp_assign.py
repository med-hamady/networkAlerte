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
sa clé UISP lui est posée **par SSH**, puis on attend qu'il se déclare avant
d'associer. Le service borne l'appel à `CALL_BUDGET_S`, sous le
`proxy_read_timeout` de la `location` dédiée dans nginx.conf — un timeout de
proxy plus court rendrait une erreur sur une adoption réussie.

Un CONTRAT DE RÉPONSE STABLE (2026-09-11)
-----------------------------------------
Toute réponse — succès comme erreur — porte `assigned`, `pending_registration`,
`retry_after_seconds` et `error_code` (`null` en cas de succès). Demandé par
l'équipe qui consomme la route, et c'est ce qui lui permet de brancher son
automate sur des champs plutôt que sur des messages.

* Le code d'erreur est un **identifiant stable** (`ERROR_CODES`), jamais un
  texte : les messages restent libres de changer, pas les codes.
* ⚠️ **Le changement est strictement ADDITIF.** Les erreurs portaient jusqu'ici
  un unique `detail` (format FastAPI) : il est conservé À L'IDENTIQUE, les codes
  HTTP aussi. Un appelant qui lit l'ancien format continue de fonctionner.
* ⚠️ L'enveloppe couvre aussi les erreurs levées AVANT l'endpoint — clé refusée
  (401) et corps invalide (422) — grâce à `_StableErrorRoute`. Elle est LOCALE à
  ce router : le format d'erreur du reste de l'API ne bouge pas.
* Changer `ERROR_CODES` ou les clés toujours présentes, c'est changer le
  contrat d'un tiers : `tests/test_uisp_assign_contract.py` le verrouille, et
  l'historique de `docs/api-uisp-assign.md` doit dire quoi et quand — AVANT de
  déployer.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import uisp_assignment_service, uisp_service

logger = logging.getLogger(__name__)

# ── Le contrat d'erreur : code STABLE -> statut HTTP ─────────────────────────
# Source unique : le statut d'une erreur se lit ICI, jamais ailleurs. Verrouillé
# par tests/test_uisp_assign_contract.py — le modifier est un changement de
# contrat pour un tiers, qui doit être annoncé avant d'être déployé.
ERROR_CODES: dict[str, int] = {
    "invalid_mac": 400,
    "uisp_not_configured": 400,
    "unauthorized": 401,
    "uisp_write_forbidden": 403,
    "crm_client_not_found": 404,
    "crm_service_mismatch": 404,
    "device_not_found": 404,
    "multiple_services": 409,
    "device_already_assigned": 409,
    "invalid_request": 422,
    "internal_error": 500,
    "device_unreachable": 502,
    "uisp_error": 502,
}


def error_response(
    error_code: str,
    message: str,
    *,
    detail: Any = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    """Enveloppe d'erreur du contrat : même forme pour TOUTES les erreurs.

    Les clés du contrat y sont à leur valeur « rien n'a été fait » : une erreur
    n'associe rien et ne laisse aucune attente en cours.
    """
    status = ERROR_CODES.get(error_code)
    if status is None:
        # Un code hors contrat est un bug chez nous : on ne le laisse pas sortir.
        logger.error("uisp_assign: code d'erreur hors contrat %r — rendu en internal_error", error_code)
        error_code, status = "internal_error", 500
    body = {
        "error_code": error_code,
        "message": message,
        "assigned": False,
        "pending_registration": False,
        "retry_after_seconds": None,
        **extra,
        # `detail` : le champ que portaient TOUTES les erreurs jusqu'ici (format
        # FastAPI). Conservé à l'identique — l'enveloppe AJOUTE, elle ne retire
        # rien à un appelant qui lit l'ancien format.
        "detail": message if detail is None else detail,
    }
    return JSONResponse(status_code=status, content=jsonable_encoder(body), headers=headers)


class _StableErrorRoute(APIRoute):
    """Route dont les erreurs levées AVANT l'endpoint sortent dans l'enveloppe.

    La clé refusée (401 — levée par la dépendance d'auth ajoutée à
    l'`include_router`) et le corps invalide (422 — levé par la validation) ne
    passent pas par le `try` de l'endpoint : sans ce wrapper, elles sortiraient
    au format FastAPI brut, sans `error_code` — le format instable même dont
    l'appelant se plaint. `include_router` conserve la classe de route, donc le
    montage dans `api/router.py` n'a rien à savoir de tout ça.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return error_response(
                    "invalid_request",
                    "Corps de requête invalide — `detail` nomme le champ en cause.",
                    detail=exc.errors(),
                )
            except HTTPException as exc:
                if exc.status_code == 401:
                    return error_response(
                        "unauthorized", str(exc.detail), detail=exc.detail, headers=exc.headers,
                    )
                # Aucune autre HTTPException n'est levée avant cet endpoint. Une
                # future dépendance qui en lèverait une garde son format natif
                # plutôt que de recevoir un code qui mentirait sur sa cause.
                raise

        return wrapped


router = APIRouter(route_class=_StableErrorRoute)


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
    # matériel. Refusé par défaut (409 device_already_assigned) : une MAC saisie
    # de travers ferait ce dégât en silence. `true` seulement si le déplacement
    # est bien l'intention.
    force: bool = Field(
        False, description="true pour déplacer un équipement DÉJÀ rattaché à un autre client",
    )
    # Ancien nom de `force`, toujours accepté : le retirer casserait un appelant
    # qui l'utilise, pour un simple renommage.
    reassign: bool = Field(False, description="Ancien nom de `force` — toujours accepté")


def _error_for(exc: Exception) -> JSONResponse:
    """Traduit une exception du service en réponse du contrat.

    Chaque `detail` reproduit à l'identique ce que la route renvoyait avant
    l'enveloppe (chaîne, ou objet pour les deux 409).
    """
    if isinstance(exc, ValueError):  # MAC mal formée
        return error_response("invalid_mac", str(exc))
    if isinstance(exc, uisp_assignment_service.AmbiguousClientError):
        return error_response(
            exc.error_code, str(exc),
            detail={"message": str(exc), "candidates": exc.candidates},
            candidates=exc.candidates,
        )
    if isinstance(exc, uisp_assignment_service.AlreadyAssignedError):
        # 409 et non 404 : rien n'est « introuvable », c'est un conflit d'état
        # que l'appelant peut lever en connaissance de cause (force=true).
        return error_response(
            exc.error_code, str(exc),
            detail={"message": str(exc), "current_crm_client_id": exc.current_crm_client_id},
            current_crm_client_id=exc.current_crm_client_id,
            current_client_name=exc.current_client_name,
        )
    if isinstance(exc, uisp_assignment_service.AssignmentError):
        # 404 (introuvables) ou 502 (équipement injoignable — surtout pas un 404,
        # qui enverrait l'appelant vérifier ses identifiants au lieu de
        # l'équipement) : le statut vient de ERROR_CODES, par le code.
        return error_response(exc.error_code, str(exc))
    if isinstance(exc, uisp_service.UISPAuthError):
        return error_response("uisp_write_forbidden", str(exc))
    logger.error("UISP assign failed: %s", exc)
    return error_response("uisp_error", f"Association UISP échouée : {exc}")


@router.post("/assign")
async def assign_to_crm_client(
    body: AssignRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Associe un équipement à un client CRM — l'équivalent du formulaire UISP.

    Transposition du geste manuel : chercher la MAC, la voir en « unknown »,
    cliquer dessus et choisir le client. Si l'équipement est absent du
    contrôleur, sa clé lui est posée d'abord, on attend qu'il se déclare (60 s
    au plus) et on l'associe dans le même appel ; sinon la réponse porte
    `pending_registration: true` et `retry_after_seconds`.

    Toute réponse porte `assigned`, `pending_registration`,
    `retry_after_seconds` et `error_code` — cf. `ERROR_CODES` pour les codes.
    """
    if not uisp_assignment_service.is_configured():
        return error_response(
            "uisp_not_configured",
            "UISP non configuré — renseigner UISP_BASE_URL et UISP_API_TOKEN "
            "(ou UISP_USERNAME/UISP_PASSWORD) dans l'environnement.",
        )
    try:
        report = await uisp_assignment_service.assign_device_to_crm_client(
            db, body.mac, body.crm_client_id, body.crm_service_id,
            reassign=body.force or body.reassign,
        )
    except Exception as exc:
        # Une erreur ANNULE la transaction, comme avant : l'ancien code levait une
        # HTTPException, que `get_db` transformait en rollback. Rendre une réponse
        # au lieu de lever la ferait COMMITTER — un changement de sémantique qui
        # n'a rien à faire dans un changement de format.
        await db.rollback()
        return _error_for(exc)
    return {**report, "error_code": None}
