import datetime

from pydantic import BaseModel


class ReportPeriodSummary(BaseModel):
    date_from: str
    date_to: str


class DeviceReliability(BaseModel):
    device_id: int
    device_name: str
    device_type: str
    location: str | None
    current_status: str
    total_incidents: int
    downtime_incidents: int
    avg_resolution_minutes: float | None


class AlertTypeFrequency(BaseModel):
    alert_type: str
    alert_type_label: str
    occurrence_count: int
    affected_device_count: int
    avg_resolution_minutes: float | None


class RadioMetrics(BaseModel):
    device_id: int
    device_name: str
    avg_signal_dbm: float | None
    min_signal_dbm: float | None
    avg_cinr_db: float | None
    avg_ccq_pct: float | None


class WeakPoint(BaseModel):
    device_id: int
    device_name: str
    pattern_description: str
    alert_type: str | None
    occurrence_count: int


class Recommendation(BaseModel):
    priority: str
    category: str
    title: str
    description: str
    affected_devices: list[str]
    alert_type: str | None


class ClientLinkHealthItem(BaseModel):
    """Verdict de santé d'un lien client LR (triage)."""

    device_id: int
    device_name: str
    verdict: str  # "critical" | "suspect"
    active_signals_count: int
    causes: list[str]
    action: str


class ClientLinkHealth(BaseModel):
    """Synthèse décisionnelle des liens clients LR.

    Réutilise exactement la classification de la page « Liaisons clients »
    (`lr_health_service.get_bad_installations`) : 5 indicateurs en moyenne
    glissante 30 j ; verdict suspect (≥3/5) ou critique (≥4/5). Les liens sains
    (<3 indicateurs) ne sont pas listés, juste résumés par `ok_count`.

    Fenêtre fixe 30 j (matview), indépendante de la plage de dates du rapport.
    """

    window_days: int
    total_clients: int
    ok_count: int
    suspect_count: int
    critical_count: int
    items: list[ClientLinkHealthItem]


class SupervisionReport(BaseModel):
    generated_at: datetime.datetime
    period: ReportPeriodSummary
    client_link_health: ClientLinkHealth
    device_reliability: list[DeviceReliability]
    alert_frequencies: list[AlertTypeFrequency]
    radio_metrics: list[RadioMetrics]
    weak_points: list[WeakPoint]
    recommendations: list[Recommendation]
