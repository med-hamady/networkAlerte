import datetime

from pydantic import BaseModel


class ReportPeriodSummary(BaseModel):
    date_from: str
    date_to: str
    total_incidents: int
    critical_count: int
    warning_count: int
    info_count: int
    open_count: int
    resolved_count: int
    acknowledged_count: int
    devices_supervised: int


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


class SupervisionReport(BaseModel):
    generated_at: datetime.datetime
    period: ReportPeriodSummary
    device_reliability: list[DeviceReliability]
    alert_frequencies: list[AlertTypeFrequency]
    radio_metrics: list[RadioMetrics]
    weak_points: list[WeakPoint]
    recommendations: list[Recommendation]
