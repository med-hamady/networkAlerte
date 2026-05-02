import datetime

from pydantic import BaseModel, ConfigDict


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    channel_id: int | None
    message: str
    status: str     # pending | sent | failed
    sent_at: datetime.datetime | None
    created_at: datetime.datetime


class AlertReadEnriched(BaseModel):
    """Alert record enriched with incident + device info (from JOIN query)."""

    id: int
    incident_id: int
    channel_id: int | None
    message: str
    status: str
    sent_at: datetime.datetime | None
    created_at: datetime.datetime
    # From incident
    incident_title: str | None
    incident_severity: str | None
    incident_alert_type: str | None
    # From device (via incident)
    device_id: int | None
    device_name: str | None
    device_ip: str | None
    # True for warning incidents waiting for the next digest batch (not yet sent)
    is_pending_digest: bool = False
