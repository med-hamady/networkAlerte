import datetime
from typing import Literal

from pydantic import BaseModel, Field

Audience = Literal["active", "blocked", "out_of_supervision"]
Channel = Literal["whatsapp", "sms"]


class BroadcastAudienceRequest(BaseModel):
    audiences: list[Audience] = Field(min_length=1)
    # Canaux d'envoi : WhatsApp, SMS, ou les deux. Défaut WhatsApp, le
    # comportement d'avant l'arrivée du SMS.
    channels: list[Channel] = Field(default_factory=lambda: ["whatsapp"], min_length=1)


class BroadcastCreateRequest(BroadcastAudienceRequest):
    message: str = Field(min_length=1, max_length=4000)


class BroadcastPreview(BaseModel):
    """Ce que l'envoi toucherait — affiché AVANT la confirmation."""

    audiences: list[Audience]
    channels: list[Channel]
    lr_count: int
    # Numéros distincts qui recevront le message (par canal choisi).
    recipient_count: int
    # LR écartés parce que leur numéro est déjà dans la liste (client à
    # plusieurs services) : ils reçoivent le message, une seule fois.
    duplicate_count: int
    # Noms de LR sans numéro exploitable : ces clients ne seront PAS prévenus.
    without_phone: list[str]
    # Durée estimée : le canal le plus lent (les canaux avancent en parallèle).
    estimated_seconds: int
    whatsapp_available: bool
    sms_available: bool


class BroadcastCounts(BaseModel):
    total: int = 0
    pending: int = 0
    sent: int = 0
    failed: int = 0
    cancelled: int = 0


class BroadcastRead(BaseModel):
    id: int
    created_at: datetime.datetime
    created_by: str | None
    finished_at: datetime.datetime | None
    status: str
    audiences: list[str]
    channels: list[str]
    message: str
    # Tous canaux confondus (un client prévenu par WhatsApp ET SMS compte deux fois).
    counts: BroadcastCounts
    by_channel: dict[str, BroadcastCounts]
    # Temps restant estimé (canal le plus lent), 0 quand rien n'attend.
    remaining_seconds: int


class BroadcastFailure(BaseModel):
    channel: str
    phone: str
    name: str | None
    error: str | None
    attempts: int


class BroadcastDetail(BroadcastRead):
    failures: list[BroadcastFailure]


class BroadcastList(BaseModel):
    broadcasts: list[BroadcastRead]
    # Envoi en cours, s'il y en a un : la page le suit en priorité.
    running_id: int | None
    delay_seconds: float
    # False = `CLIENT_BROADCAST_ENABLED=false` : la page annonce le service
    # arrêté et ne propose plus aucun envoi.
    service_enabled: bool = True
