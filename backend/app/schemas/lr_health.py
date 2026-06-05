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

    # Per-LR floors actually applied (distance-banding removed 2026-05-21;
    # link_potential and rx_rate floors are family-banded LTU vs airMAX).
    signal_warning_threshold: float       # flat — settings.signal_warning_dbm
    link_potential_floor_pct: float       # family floor
    total_capacity_floor_mbps: float
    rx_rate_floor_idx: float              # family floor


class BadInstallationsResponse(BaseModel):
    period_days: int
    generated_at: datetime.datetime
    items: list[BadInstallationRow]


class LiveLinkHealthResponse(BaseModel):
    """Réponse de la page « Liaisons clients » en mode **live** (état actuel).

    Pas de ``period_days`` : les indicateurs sont évalués sur les valeurs
    interrogées en direct à l'ouverture de la page, pas sur une fenêtre.
    ``unreachable_count`` = nombre de LR exclus faute d'avoir pu être joints
    en live (lien down, auth, timeout, creds manquants)."""

    generated_at: datetime.datetime
    unreachable_count: int
    items: list[BadInstallationRow]
