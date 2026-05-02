import datetime
import ipaddress
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

# Single source of truth for accepted device_type values. Any new device class
# must be added here and to the polling jobs at the same time.
DeviceType = Literal["ltu_rocket", "ltu_lr", "uisp_switch", "uisp_power", "airmax_rocket"]

# Accepts MAC formats: aa:bb:cc:dd:ee:ff, AA-BB-CC-DD-EE-FF, AABBCCDDEEFF, aabb.ccdd.eeff.
# Normalised to lowercase colon notation.
_MAC_RAW = re.compile(r"^[0-9A-Fa-f]{12}$")
_MAC_SEP = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")
_MAC_DOT = re.compile(r"^([0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}$")


def _validate_ip(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"Invalid IP address: {value!r}") from exc
    return value


def normalize_mac(value: str) -> str:
    """Normalise a MAC address to lowercase colon-separated form (aa:bb:...)."""
    raw = value.strip()
    if _MAC_SEP.match(raw):
        clean = raw.replace("-", ":").lower()
    elif _MAC_DOT.match(raw):
        hex_only = raw.replace(".", "").lower()
        clean = ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))
    elif _MAC_RAW.match(raw):
        hex_only = raw.lower()
        clean = ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))
    else:
        raise ValueError(f"Invalid MAC address: {value!r}")
    return clean


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
    mac_address: str | None = None
    hostname: str | None = None
    firmware_version: str | None = None

    @field_validator("ip_address")
    @classmethod
    def _check_ip(cls, v: str) -> str:
        return _validate_ip(v)

    @field_validator("mac_address")
    @classmethod
    def _check_mac(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return normalize_mac(v)


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
    mac_address: str | None = None
    hostname: str | None = None
    firmware_version: str | None = None

    @field_validator("ip_address")
    @classmethod
    def _check_ip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_ip(v)

    @field_validator("mac_address")
    @classmethod
    def _check_mac(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return normalize_mac(v)


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
    mac_address: str | None = None
    hostname: str | None = None
    firmware_version: str | None = None
    auto_discovered: bool = False
    first_discovered_at: datetime.datetime | None = None
    last_discovered_at: datetime.datetime | None = None

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "DeviceRead":
        instance = super().model_validate(obj, **kwargs)
        # Derive has_ssh_password from the ORM object without exposing the value
        if hasattr(obj, "ssh_password"):
            instance.has_ssh_password = bool(obj.ssh_password)
        return instance
