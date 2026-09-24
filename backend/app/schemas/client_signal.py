"""Schéma de la réponse de GET /client-signal — qualité du signal d'un client.

Contrat exposé à un système tiers : on lui passe le MAC du LR client, il reçoit

  - une catégorie qualitative du **signal** (`excellent` / `bien` / `moyen` /
    `faible`) calculée à partir de la dernière valeur connue en base ;
  - une mesure de **latence faite EN DIRECT** à l'appel (le LR ping Internet,
    5 paquets de 56 o) avec sa propre catégorie et sa description.

Voir :mod:`app.services.client_signal_service`.
"""

import datetime

from pydantic import BaseModel, Field


class ConnectedRocket(BaseModel):
    """Le Rocket (point d'accès) auquel le LR est rattaché.

    ``source`` dit d'où vient le rattachement : ``supervision`` = la fiche du
    Rocket existe chez nous (``lrs.rocket_id``, arbitré entre la découverte radio
    et UISP — « le dernier qui a vu la station gagne ») ; ``uisp`` = on ne
    connaît que le NOM de l'AP annoncé par le contrôleur (Rocket non supervisé
    ou rattachement pas encore établi) — les autres champs sont alors ``null``.
    """

    id: int | None = Field(default=None, description="Identifiant interne du Rocket")
    name: str | None = Field(default=None, description="Nom du Rocket")
    mac: str | None = Field(default=None, description="MAC du Rocket")
    ip_address: str | None = Field(default=None, description="IP de management du Rocket")
    site: str | None = Field(default=None, description="Site du Rocket")
    radio_tech: str | None = Field(default=None, description="Famille radio : ltu | airmax")
    status: str | None = Field(
        default=None, description="Joignabilité du Rocket (up/down/unknown)"
    )
    source: str = Field(description="Origine du rattachement : supervision | uisp")


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
    latency_avg_ms: float | None = Field(
        default=None,
        description="Latence moyenne du LR vers Internet, mesurée À L'APPEL "
        "(None si le LR est injoignable ou sans transit)",
    )
    latency_quality: str = Field(
        description="Catégorie de latence : excellent | tres_bien | bien | mauvaise "
        "| catastrophique | indetermine",
    )
    latency_message: str = Field(
        description="Description lisible de la latence, ex. « Latence excellente (42 ms) »",
    )
    latency_target: str | None = Field(
        default=None, description="Cible pingée depuis le LR (ex. 8.8.8.8)"
    )
    latency_packets_sent: int | None = Field(
        default=None, description="Nombre de paquets ICMP envoyés pour la mesure"
    )
    latency_packet_size_bytes: int | None = Field(
        default=None, description="Taille de la charge utile ICMP en octets"
    )
    rocket: ConnectedRocket | None = Field(
        default=None,
        description="Rocket auquel le LR est connecté (null si aucun rattachement connu)",
    )
