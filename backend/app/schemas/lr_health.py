import datetime

from pydantic import BaseModel, ConfigDict


class SignalEvidence(BaseModel):
    """One independent diagnostic indicator evaluated against an LR.

    The 5 indicators together yield the verdict. `active` is the binary
    "this indicator fired" outcome; `value` and `detail` give the operator
    the raw numbers so a verdict can be explained, not just trusted.
    """

    key: str
    label: str
    active: bool
    value: str
    detail: str


class BadInstallationRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Identity
    lr_id: int
    lr_name: str
    lr_ip: str | None          # ip_address est NULLABLE depuis l'identité LR par MAC
    lr_mac: str | None
    model_variant: str
    distance_m: float | None
    first_discovered_at: datetime.datetime | None
    rocket_id: int | None
    rocket_name: str | None

    # Verdict (out of 5 indicators)
    verdict: str               # suspect | critical
    active_signals_count: int
    signals: list[SignalEvidence]

    # Latest values of the metrics behind the 5 indicators
    latest_signal_dbm: float | None
    latest_link_potential_pct: float | None
    latest_total_capacity_mbps: float | None
    latest_local_rx_rate_idx: float | None
    latest_remote_rx_rate_idx: float | None

    # LR → Internet RTT (ms) — dernier relevé de lr_internet_probe_job (sonde
    # SSH 60 s). Affichage seulement, n'entre pas dans le verdict. None si le LR
    # n'a pas de mesure récente (pas de transit, sonde KO) ou côté rapport 30 j.
    latency_ms: float | None = None

    # Per-LR floors actually applied (distance-banding removed 2026-05-21;
    # link_potential and rx_rate floors are family-banded LTU vs airMAX).
    signal_warning_threshold: float       # flat — settings.signal_warning_dbm
    link_potential_floor_pct: float       # family floor
    total_capacity_floor_mbps: float
    rx_rate_floor_idx: float              # family floor


class LiveLinkHealthResponse(BaseModel):
    """Réponse de la page « Liaisons clients » en mode **live** (état actuel).

    Pas de ``period_days`` : les indicateurs sont évalués sur les valeurs
    interrogées en direct à l'ouverture de la page, pas sur une fenêtre.
    ``unreachable_count`` = nombre de LR exclus faute d'avoir pu être joints
    en live (lien down, auth, timeout, creds manquants)."""

    generated_at: datetime.datetime
    unreachable_count: int
    items: list[BadInstallationRow]


class SiteLinkRow(BaseModel):
    """Une liaison backhaul point-à-point entre deux sites (airFiber 60).

    Critère unique : la **dernière capacité totale** lue en base est sous le
    plancher d'affichage (``af60_capacity_display_min_mbps``, 1.95 Gb/s). Pas de
    fetch live (trop coûteux) — on relit la dernière valeur de ``device_metrics``.
    Signal/SNR sont joints uniquement pour l'affichage, jamais pour le filtre."""

    model_config = ConfigDict(from_attributes=True)

    device_id: int
    name: str
    ip: str | None             # ip_address est NULLABLE (identité par MAC)
    distance_m: float | None

    # Critère unique : capacité totale (Mbps) vs plancher d'affichage.
    latest_total_capacity_mbps: float | None
    capacity_floor_mbps: float

    # Affichage seul (dernières valeurs en base), hors filtre.
    latest_signal_dbm: float | None
    latest_snr_db: float | None


class SiteLinkHealthResponse(BaseModel):
    """Réponse de la section « Liaisons entre sites (P2P) ».

    Liens backhaul AF60 dont la dernière capacité totale est sous le plancher
    d'affichage (1.95 Gb/s par défaut). Lecture de la dernière valeur en base,
    pas d'interrogation live. ``no_data_count`` = AF60 sans relevé de capacité."""

    generated_at: datetime.datetime
    no_data_count: int
    items: list[SiteLinkRow]
