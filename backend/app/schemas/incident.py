import datetime

from pydantic import BaseModel, ConfigDict

from app.core.alert_constants import NotificationEvent
from app.models.device import Device
from app.models.incident import Incident
from app.services import alert_formatter
from app.services.alert_policy import (
    effective_notify_immediately,
    get_policy_for_device,
)


class IncidentRead(BaseModel):
    """
    Standard alert/incident view exposed by the API.

    Persisted fields come from the Incident row.
    Device fields and the policy-derived fields (notify_immediately,
    notification_channel_policy, message) are populated by `from_incident()`
    so that per-device overrides on Device.policy_overrides are taken into
    account.
    """

    model_config = ConfigDict(from_attributes=True)

    # --- Persisted incident fields ---
    id: int
    device_id: int
    title: str
    description: str | None
    severity: str   # info | warning | critical
    status: str     # open | acknowledged | resolved
    detected_at: datetime.datetime
    resolved_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    # --- Alert engine fields (already persisted in 'incidents') ---
    alert_type: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    threshold_value: float | None = None
    last_triggered_at: datetime.datetime | None = None

    # --- Device fields (joined, filled by from_incident) ---
    device_name: str | None = None
    device_type: str | None = None
    device_ip: str | None = None
    device_mac: str | None = None
    # LR-only: specific model variant (ltu_lr / litebeam_5ac / ...) so the UI can
    # show whether the subscriber radio is LTU or airMAX. None for non-LR devices.
    lr_model_variant: str | None = None

    # --- Pre-formatted human-readable message ---
    message: str | None = None

    # --- Policy-derived fields (filled by from_incident, fallback to global) ---
    notify_immediately: bool = False
    notification_channel_policy: list[str] = []

    @classmethod
    def from_incident(
        cls,
        incident: Incident,
        device: Device | None = None,
    ) -> "IncidentRead":
        """
        Build the schema from an Incident plus its (already loaded) Device.

        Per-device overrides are applied via get_policy_for_device when
        the device is available; otherwise the global policy is used.
        """
        instance = cls.model_validate(incident)

        overrides = getattr(device, "policy_overrides", None) if device is not None else None
        policy = get_policy_for_device(incident.alert_type, overrides)

        instance.notify_immediately = effective_notify_immediately(policy, incident.severity)
        instance.notification_channel_policy = list(policy.channels)

        if device is not None:
            instance.device_name = device.name
            instance.device_type = device.device_type
            instance.device_ip = device.ip_address
            instance.device_mac = device.mac_address
            instance.lr_model_variant = getattr(device, "model_variant", None)
            event = (
                NotificationEvent.RESOLVED
                if incident.status == "resolved"
                else NotificationEvent.OPENED
            )
            instance.message = alert_formatter.format_human_readable(
                device, incident, event,
            )

        return instance
