import datetime

from pydantic import BaseModel, ConfigDict

from app.models.device import Device
from app.models.manual_alert import ManualAlert


class ManualAlertRead(BaseModel):
    """Une ligne du bandeau d'anomalies à acquitter."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    alert_type: str
    severity: str          # info | warning | critical
    title: str
    description: str | None
    detected_at: datetime.datetime
    acknowledged_at: datetime.datetime | None
    acknowledged_by: str | None

    # --- Champs d'équipement (joints) ---
    device_name: str | None = None
    device_type: str | None = None
    device_ip: str | None = None
    device_site: str | None = None

    @classmethod
    def from_alert(
        cls,
        alert: ManualAlert,
        device: Device | None = None,
    ) -> "ManualAlertRead":
        instance = cls.model_validate(alert)
        if device is not None:
            instance.device_name = device.name
            instance.device_type = device.device_type
            instance.device_ip = device.ip_address
            instance.device_site = device.site
        return instance


class ManualAlertList(BaseModel):
    """Payload du bandeau : les anomalies en attente et leur décompte."""

    alerts: list[ManualAlertRead]
    count: int
