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
    lr_ip: str
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

    Contrairement à ``BadInstallationRow`` (LR client rattaché à un Rocket), un
    AF60 est un équipement d'infra autonome : pas de parent, un seul peer (l'autre
    extrémité du lien). Le scoring utilise **4 indicateurs** propres au 60 GHz
    (signal, SNR, potentiel, capacité) avec les seuils ``af60_*`` — le SNR remplace
    les 2 indicateurs de débit-idx des LR (pas de plancher 60 GHz défini)."""

    model_config = ConfigDict(from_attributes=True)

    # Identity
    device_id: int
    name: str
    ip: str
    distance_m: float | None

    # Verdict (out of 4 indicators — suspect ≥2, critical ≥3)
    verdict: str               # suspect | critical
    active_signals_count: int
    total_indicators: int
    signals: list[SignalEvidence]

    # Latest values behind the indicators (+ remote signal, affichage seul)
    latest_signal_dbm: float | None
    latest_snr_db: float | None
    latest_remote_signal_dbm: float | None
    latest_link_potential_pct: float | None
    latest_total_capacity_mbps: float | None

    # Floors actually applied (AF60-specific settings)
    signal_warning_threshold: float
    snr_warning_threshold: float
    link_potential_floor_pct: float
    total_capacity_floor_mbps: float


class SiteLinkHealthResponse(BaseModel):
    """Réponse de la section « Liaisons entre sites (P2P) » en mode **live**.

    Même sémantique live que ``LiveLinkHealthResponse`` mais pour les liens
    backhaul AF60 : on n'affiche que les liaisons dégradées (≥2/4 indicateurs).
    ``unreachable_count`` = AF60 exclus faute d'avoir pu être joints en direct."""

    generated_at: datetime.datetime
    unreachable_count: int
    items: list[SiteLinkRow]
