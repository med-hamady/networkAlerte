"""Schéma de la réponse de GET /client-signal — qualité du signal d'un client.

Contrat exposé à un système tiers : on lui passe le MAC du LR client, il reçoit
une catégorie qualitative (`excellent` / `bien` / `moyen` / `faible`) calculée à
partir de la dernière valeur de signal connue en base. Voir
:mod:`app.services.client_signal_service`.
"""

import datetime

from pydantic import BaseModel, Field


class ClientSignalResponse(BaseModel):
    mac: str = Field(description="MAC du LR client, normalisé (aa:bb:cc:dd:ee:ff)")
    lr_id: int = Field(description="Identifiant interne du LR")
    lr_name: str | None = Field(default=None, description="Nom du LR")
    status: str = Field(description="État de joignabilité du LR (up/down/unknown)")
    signal_dbm: float | None = Field(
        default=None, description="Dernière valeur de signal en dBm (None si aucune mesure)"
    )
    quality: str = Field(description="Catégorie : excellent | bien | moyen | faible | indetermine")
    message: str = Field(description="Libellé lisible, ex. « Signal excellent »")
    measured_at: datetime.datetime | None = Field(
        default=None, description="Horodatage de la dernière mesure de signal (fraîcheur)"
    )
