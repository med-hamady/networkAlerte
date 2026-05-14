import datetime

from pydantic import BaseModel, ConfigDict


class SignalEvidence(BaseModel):
    """One independent diagnostic signal evaluated against an LR.

    The 10 signals together yield the verdict. `active` is the binary
    "this signal fired" outcome; `value` and `detail` give the operator
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

    # Verdict
    verdict: str               # watch | suspect | critical
    active_signals_count: int
    signals: list[SignalEvidence]

    # Downtime context (the only "incident" type that matters for this view)
    outages_count: int
    downtime_hours: float

    # Latest values of the four tracked physical metrics
    latest_signal_dbm: float | None
    latest_noise_dbm: float | None
    latest_ccq_pct: float | None

    # Threshold actually used for this LR (depends on distance band)
    signal_warning_threshold: float


class BadInstallationsResponse(BaseModel):
    period_days: int
    generated_at: datetime.datetime
    items: list[BadInstallationRow]
