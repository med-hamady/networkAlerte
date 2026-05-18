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
    verdict: str               # watch | suspect | critical
    active_signals_count: int
    signals: list[SignalEvidence]

    # Latest values of the metrics behind the 5 indicators
    latest_signal_dbm: float | None
    latest_link_potential_pct: float | None
    latest_total_capacity_mbps: float | None
    latest_local_rx_rate_idx: float | None
    latest_remote_rx_rate_idx: float | None

    # Distance-banded signal threshold actually used for this LR
    signal_warning_threshold: float


class BadInstallationsResponse(BaseModel):
    period_days: int
    generated_at: datetime.datetime
    items: list[BadInstallationRow]
