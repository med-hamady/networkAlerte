"""Suppression / remplacement d'une antenne abonnée — l'API de S-Ticketing.

Router à son PROPRE préfixe (`/antennas`) et à sa propre clé
(`TICKETING_API_KEY`, dépendance `require_ticketing_client`). ⚠️ Une dépendance
de router étant ADDITIVE, toute route ajoutée dans ce fichier s'ouvrirait à
cette clé en silence : n'y mettre que ce que S-Ticketing a le droit de faire.

⚠️ Les deux routes ÉCRIVENT dans le contrôleur UISP (token d'écriture) et
SUPPRIMENT des lignes de notre base. Les garde-fous (infra refusée, antenne en
ligne, client bloqué, propriétaire) vivent dans `antenna_lifecycle_service`.

Contrat de réponse stable, sur le modèle de `/uisp/assign` : toute réponse —
succès comme erreur — porte `error_code` (`null` en cas de succès), un code
STABLE de `ERROR_CODES`, et `message`. Les erreurs levées avant l'endpoint (clé
refusée, corps invalide) sortent dans la même enveloppe.
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
from app.services import antenna_lifecycle_service, uisp_assignment_service, uisp_service

logger = logging.getLogger(__name__)

# Code STABLE -> statut HTTP. Source unique ; verrouillé par
# tests/test_antennas_api.py — le changer, c'est changer le contrat de S-Ticketing.
ERROR_CODES: dict[str, int] = {
    "invalid_mac": 400,
    "same_device": 400,
    "uisp_not_configured": 400,
    "unauthorized": 401,
    "uisp_write_forbidden": 403,
    "device_not_found": 404,
    "old_device_not_found": 404,
    "crm_client_not_found": 404,
    "crm_service_mismatch": 404,
    "not_a_client_antenna": 409,
    "device_online": 409,
    "client_blocked": 409,
    "content_filter_active": 409,
    "old_device_not_owned": 409,
    "old_device_owner_unknown": 409,
    "device_already_assigned": 409,
    "multiple_services": 409,
    "invalid_request": 422,
    "internal_error": 500,
    "device_unreachable": 502,
    "old_device_delete_failed": 502,
    "uisp_error": 502,
}


def error_response(
    error_code: str, message: str, *, detail: Any = None,
    headers: dict[str, str] | None = None, **extra: Any,
) -> JSONResponse:
    status = ERROR_CODES.get(error_code)
    if status is None:
        logger.error("antennas: code d'erreur hors contrat %r — rendu en internal_error", error_code)
        error_code, status = "internal_error", 500
    body = {"error_code": error_code, "message": message, **extra}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=jsonable_encoder(body), headers=headers)


class _StableErrorRoute(APIRoute):
    """Clé refusée (401) et corps invalide (422) dans l'enveloppe du contrat."""

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
                    return error_response("unauthorized", str(exc.detail), headers=exc.headers)
                raise

        return wrapped


router = APIRouter(route_class=_StableErrorRoute)


class DeleteRequest(BaseModel):
    mac: str = Field(..., description="MAC de l'antenne à supprimer (toute notation)")
    force: bool = Field(
        False, description="true pour supprimer une antenne encore vue EN LIGNE",
    )


class ReplaceRequest(BaseModel):
    crm_client_id: str = Field(..., description="Id du client dans le CRM")
    old_mac: str = Field(..., description="MAC de l'antenne retirée")
    new_mac: str = Field(..., description="MAC de l'antenne installée")
    crm_service_id: str | None = Field(
        None,
        description="Id du service CRM — seulement si le client en a plusieurs ET que "
                    "l'ancienne antenne n'est pas dans UISP (sinon son service est repris)",
    )
    force: bool = Field(
        False, description="true pour remplacer alors que l'ANCIENNE antenne est encore en ligne",
    )


def _error_for(exc: Exception) -> JSONResponse:
    if isinstance(exc, ValueError):  # MAC mal formée
        return error_response("invalid_mac", str(exc))
    if isinstance(exc, antenna_lifecycle_service.AntennaError):
        return error_response(exc.error_code, str(exc), **exc.extra)
    if isinstance(exc, uisp_assignment_service.AmbiguousClientError):
        return error_response(exc.error_code, str(exc), candidates=exc.candidates)
    if isinstance(exc, uisp_assignment_service.AlreadyAssignedError):
        return error_response(
            exc.error_code, str(exc),
            current_crm_client_id=exc.current_crm_client_id,
            current_client_name=exc.current_client_name,
        )
    if isinstance(exc, uisp_assignment_service.AssignmentError):
        return error_response(exc.error_code, str(exc))
    if isinstance(exc, uisp_service.UISPAuthError):
        return error_response("uisp_write_forbidden", str(exc))
    logger.error("antennas: opération échouée : %s", exc)
    return error_response("uisp_error", f"Opération UISP échouée : {exc}")


def _not_configured() -> JSONResponse:
    return error_response(
        "uisp_not_configured",
        "UISP non configuré — renseigner UISP_BASE_URL et UISP_API_TOKEN dans l'environnement.",
    )


@router.post("/delete")
async def delete_antenna(body: DeleteRequest, db: AsyncSession = Depends(get_db)) -> Any:
    """Supprime une antenne abonnée de UISP Network puis d'A2 Supervision."""
    if not uisp_assignment_service.is_configured():
        return _not_configured()
    try:
        report = await antenna_lifecycle_service.delete_antenna(db, body.mac, force=body.force)
    except Exception as exc:
        await db.rollback()
        return _error_for(exc)
    return {**report, "error_code": None}


@router.post("/replace")
async def replace_antenna(body: ReplaceRequest, db: AsyncSession = Depends(get_db)) -> Any:
    """Remplace l'antenne d'un client : rattache la nouvelle, supprime l'ancienne.

    Rejouable : si la nouvelle n'est pas encore déclarée au contrôleur
    (`pending_registration`), l'ancienne est conservée et l'appel se rejoue après
    `retry_after_seconds`.
    """
    if not uisp_assignment_service.is_configured():
        return _not_configured()
    try:
        report = await antenna_lifecycle_service.replace_antenna(
            db, body.crm_client_id, body.old_mac, body.new_mac,
            crm_service_id=body.crm_service_id, force=body.force,
        )
    except Exception as exc:
        # ⚠️ Un échec APRÈS le rattachement (old_device_delete_failed) annule
        # seulement notre écriture locale : UISP a déjà enregistré la nouvelle
        # antenne, et le rejeu la trouve rattachée (no-op) avant de supprimer.
        await db.rollback()
        return _error_for(exc)
    return {**report, "error_code": None}
