import datetime
import ipaddress
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

# Single source of truth for accepted device_type values. Any new device class
# must be added here and to the polling jobs at the same time.
DeviceType = Literal["ltu_rocket", "ltu_lr", "uisp_switch", "uisp_power", "airmax_rocket"]


def _validate_ip(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"Invalid IP address: {value!r}") from exc
    return value


class DeviceCreate(BaseModel):
    """Schema for creating a new device."""

    name: str
    ip_address: str
    device_type: DeviceType
    model: str | None = None
    status: str = "unknown"
    location: str | None = None
    snmp_community: str | None = None
    ssh_username: str | None = None
    ssh_password: str | None = None   # stored per-device, takes priority over global .env credential
    ssh_port: int = 22
    ssh_host_fingerprint: str | None = None
    notes: str | None = None
    parent_id: int | None = None
    policy_overrides: dict[str, Any] | None = None

    @field_validator("ip_address")
    @classmethod
    def _check_ip(cls, v: str) -> str:
        return _validate_ip(v)


class DeviceUpdate(BaseModel):
    """Schema for updating a device (all fields optional)."""

    name: str | None = None
    ip_address: str | None = None
    device_type: DeviceType | None = None
    model: str | None = None
    status: str | None = None
    location: str | None = None
    snmp_community: str | None = None
    ssh_username: str | None = None
    ssh_password: str | None = None   # None = keep existing password
    ssh_port: int | None = None
    ssh_host_fingerprint: str | None = None
    notes: str | None = None
    parent_id: int | None = None
    policy_overrides: dict[str, Any] | None = None

    @field_validator("ip_address")
    @classmethod
    def _check_ip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_ip(v)


class DeviceRead(BaseModel):
    """Schema for reading a device. ssh_password is intentionally excluded (write-only)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ip_address: str
    device_type: str
    model: str | None
    status: str
    location: str | None
    snmp_community: str | None
    ssh_username: str | None
    ssh_port: int
    ssh_host_fingerprint: str | None = None
    has_ssh_password: bool = False    # true if a per-device password is stored (password itself is not returned)
    notes: str | None
    last_seen: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    parent_id: int | None
    policy_overrides: dict[str, Any] | None = None

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "DeviceRead":
        instance = super().model_validate(obj, **kwargs)
        # Derive has_ssh_password from the ORM object without exposing the value
        if hasattr(obj, "ssh_password"):
            instance.has_ssh_password = bool(obj.ssh_password)
        return instance
