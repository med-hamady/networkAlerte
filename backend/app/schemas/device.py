"""Pydantic schemas for the four device types.

The endpoints use a discriminated union on `device_type` so a single POST
endpoint can accept any of the four subtype payloads with proper validation.

Naming: `*Create`/`*Update`/`*Read` per type. The polymorphic responses are
`DeviceRead` (the discriminated union of *Read).
"""

import datetime
import ipaddress
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

# Discriminator values used by SQLAlchemy polymorphic identity and by the API.
DeviceType = Literal[
    "rocket", "lr", "uisp_power", "uisp_switch", "client_modem", "airfiber", "ptp_litebeam"
]

ManagementProtocol = Literal["ssh", "telnet"]

RocketRadioTech = Literal["ltu", "airmax"]

# 3 LTU variants + 2 airMAX (Litebeam) variants.
LrModelVariant = Literal[
    "ltu_lr",
    "ltu_instant",
    "ltu_lite",
    "litebeam_5ac",
    "litebeam_m5",
]

# MAC formats: aa:bb:cc:dd:ee:ff, AA-BB-CC-DD-EE-FF, AABBCCDDEEFF, aabb.ccdd.eeff.
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


# ─────────────────────────────────────────────────────────────────────────────
# Common base mixins — shared columns on `devices`
# ─────────────────────────────────────────────────────────────────────────────


class _DeviceBaseCreate(BaseModel):
    """Shared fields accepted at creation across all device types."""

    name: str
    ip_address: str
    location: str | None = None
    snmp_community: str | None = None
    notes: str | None = None
    mac_address: str | None = None
    hostname: str | None = None
    firmware_version: str | None = None
    policy_overrides: dict[str, Any] | None = None

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


class _DeviceBaseUpdate(BaseModel):
    """Shared fields on PUT — all optional, omitted = keep existing."""

    name: str | None = None
    ip_address: str | None = None
    location: str | None = None
    status: str | None = None
    snmp_community: str | None = None
    notes: str | None = None
    mac_address: str | None = None
    hostname: str | None = None
    firmware_version: str | None = None
    policy_overrides: dict[str, Any] | None = None

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


# ─────────────────────────────────────────────────────────────────────────────
# Rocket
# ─────────────────────────────────────────────────────────────────────────────


class RocketCreate(_DeviceBaseCreate):
    device_type: Literal["rocket"] = "rocket"
    radio_tech: RocketRadioTech
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int = 443
    # Manual client-capacity ceiling. None = auto formula (per family/width).
    max_clients_override: int | None = None


class RocketUpdate(_DeviceBaseUpdate):
    radio_tech: RocketRadioTech | None = None
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int | None = None
    # Manual rocket_client_overload ceiling. None sent explicitly = clear the
    # override (back to the auto formula); omitted = keep existing.
    max_clients_override: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# LR
# ─────────────────────────────────────────────────────────────────────────────


class LrCreate(_DeviceBaseCreate):
    device_type: Literal["lr"] = "lr"
    model_variant: LrModelVariant
    rocket_id: int | None = None
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int = 22
    distance_m: float | None = None


class LrUpdate(_DeviceBaseUpdate):
    # model_variant is intentionally NOT here: it's set by auto-discovery from
    # the Rocket's reported model string and stays immutable. If detection is
    # wrong, fix _infer_model_variant in discovery_service.
    # rocket_id is also not exposed: re-parenting is driven by the discovery
    # pipeline (the LR shows up as a peer of a different Rocket).
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int | None = None
    # Operator may correct the LAN port name per device (rare: almost always
    # eth0). The block/unblock *flags* are intentionally NOT settable here —
    # they go through the dedicated endpoints so the SSH shutdown actually
    # runs. A PUT-set boolean with nothing enforcing it was the is_suspended
    # mistake. ssh_service refuses radio/management interfaces regardless.
    lan_interface: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# UISP Power
# ─────────────────────────────────────────────────────────────────────────────


class UispPowerCreate(_DeviceBaseCreate):
    device_type: Literal["uisp_power"] = "uisp_power"
    api_username: str | None = None
    api_password: str | None = None
    api_port: int = 443


class UispPowerUpdate(_DeviceBaseUpdate):
    api_username: str | None = None
    api_password: str | None = None
    api_port: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# UISP Switch
# ─────────────────────────────────────────────────────────────────────────────


class UispSwitchCreate(_DeviceBaseCreate):
    device_type: Literal["uisp_switch"] = "uisp_switch"
    max_ports: int = 16
    rocket_port_index: int | None = None
    port_min_speed_mbps: float = 1000.0


class UispSwitchUpdate(_DeviceBaseUpdate):
    max_ports: int | None = None
    rocket_port_index: int | None = None
    port_min_speed_mbps: float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# airFiber 60 (AF60-LR) — lien backhaul 60 GHz. Mêmes creds API que Rocket.
# ─────────────────────────────────────────────────────────────────────────────


class AirFiberCreate(_DeviceBaseCreate):
    device_type: Literal["airfiber"] = "airfiber"
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int = 443


class AirFiberUpdate(_DeviceBaseUpdate):
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# PTP LiteBeam — LiteBeam airMAX en lien point-à-point inter-sites.
# Mêmes creds airOS que les LR airMAX. Type infra dédié (ni Rocket ni LR).
# ─────────────────────────────────────────────────────────────────────────────


class PtpLiteBeamCreate(_DeviceBaseCreate):
    device_type: Literal["ptp_litebeam"] = "ptp_litebeam"
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int = 443
    distance_m: float | None = None


class PtpLiteBeamUpdate(_DeviceBaseUpdate):
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Client modem — TP-Link / Huawei / ZTE behind an LR (NAT)
# Inventoried only; reachability is probed from the parent LR (ping-from-LR).
# ─────────────────────────────────────────────────────────────────────────────


class ClientModemCreate(_DeviceBaseCreate):
    device_type: Literal["client_modem"] = "client_modem"
    lr_id: int | None = None
    management_protocol: ManagementProtocol = "ssh"
    management_port: int = 22
    management_username: str | None = None
    management_password: str | None = None


class ClientModemUpdate(_DeviceBaseUpdate):
    lr_id: int | None = None
    management_protocol: ManagementProtocol | None = None
    management_port: int | None = None
    management_username: str | None = None
    management_password: str | None = None


# Discriminated union — FastAPI parses based on the `device_type` field.
# LrCreate is INTENTIONALLY excluded: LRs are only ever created by the
# auto-discovery pipeline (see services/discovery_service.reconcile_peers).
# A POST /devices with device_type="lr" therefore fails validation (422).
DeviceCreate = (
    RocketCreate | UispPowerCreate | UispSwitchCreate | ClientModemCreate
    | AirFiberCreate | PtpLiteBeamCreate
)
DeviceUpdate = (
    RocketUpdate
    | LrUpdate
    | UispPowerUpdate
    | UispSwitchUpdate
    | ClientModemUpdate
    | AirFiberUpdate
    | PtpLiteBeamUpdate
)


# ─────────────────────────────────────────────────────────────────────────────
# Read schemas — never return passwords, only the has_* flags.
# ─────────────────────────────────────────────────────────────────────────────


class _DeviceBaseRead(BaseModel):
    """Common shape — every *Read inherits this."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_type: str
    name: str
    # Nullable: a stale LR binding is freed (NULL) during DHCP churn until its
    # own Rocket rediscovers it. Operator-created devices always have an IP.
    ip_address: str | None
    status: str
    location: str | None
    # Denormalised site (parent Rocket's location for an LR, own location
    # otherwise) maintained by DB triggers — lets the /sites drill-down filter
    # by site without re-resolving the hierarchy client-side.
    site: str | None = None
    snmp_community: str | None
    notes: str | None
    last_seen: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    mac_address: str | None = None
    hostname: str | None = None
    firmware_version: str | None = None
    auto_discovered: bool = False
    first_discovered_at: datetime.datetime | None = None
    last_discovered_at: datetime.datetime | None = None
    policy_overrides: dict[str, Any] | None = None


class RocketRead(_DeviceBaseRead):
    device_type: Literal["rocket"] = "rocket"
    radio_tech: str
    max_clients_override: int | None = None
    ssh_username: str | None = None
    ssh_port: int = 443
    ssh_host_fingerprint: str | None = None
    has_ssh_password: bool = False

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "RocketRead":
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "ssh_password"):
            instance.has_ssh_password = bool(obj.ssh_password)
        return instance


class LrRead(_DeviceBaseRead):
    device_type: Literal["lr"] = "lr"
    model_variant: str
    rocket_id: int | None = None
    ssh_username: str | None = None
    ssh_port: int = 22
    ssh_host_fingerprint: str | None = None
    has_ssh_password: bool = False
    distance_m: float | None = None
    client_blocked: bool = False
    client_blocked_at: datetime.datetime | None = None
    client_blocked_reason: str | None = None
    lan_interface: str = "eth0"
    client_block_enforced_at: datetime.datetime | None = None
    block_mode: str = "full"
    # Per-category content filter (independent of block_mode). None/[] = none;
    # coerced to [] in model_validate so the frontend always gets a list.
    blocked_categories: list[str] | None = None
    content_block_enforced_at: datetime.datetime | None = None
    topology_mode: str = "unknown"  # "router" | "bridge" | "unknown"
    # Subscription plan (forfait) cached from the LR's traffic shaper via SSH.
    # None/None = never synced or no shaper on the device. Name is CRM-only.
    plan_download_mbps: float | None = None
    plan_upload_mbps: float | None = None
    plan_synced_at: datetime.datetime | None = None

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "LrRead":
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "ssh_password"):
            instance.has_ssh_password = bool(obj.ssh_password)
        if instance.blocked_categories is None:
            instance.blocked_categories = []
        return instance


class UispPowerRead(_DeviceBaseRead):
    device_type: Literal["uisp_power"] = "uisp_power"
    api_username: str | None = None
    api_port: int = 443
    has_api_password: bool = False

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "UispPowerRead":
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "api_password"):
            instance.has_api_password = bool(obj.api_password)
        return instance


class UispSwitchRead(_DeviceBaseRead):
    device_type: Literal["uisp_switch"] = "uisp_switch"
    max_ports: int = 16
    rocket_port_index: int | None = None
    port_min_speed_mbps: float = 1000.0


class AirFiberRead(_DeviceBaseRead):
    device_type: Literal["airfiber"] = "airfiber"
    ssh_username: str | None = None
    ssh_port: int = 443
    ssh_host_fingerprint: str | None = None
    has_ssh_password: bool = False
    distance_m: float | None = None

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "AirFiberRead":
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "ssh_password"):
            instance.has_ssh_password = bool(obj.ssh_password)
        return instance


class PtpLiteBeamRead(_DeviceBaseRead):
    device_type: Literal["ptp_litebeam"] = "ptp_litebeam"
    ssh_username: str | None = None
    ssh_port: int = 443
    ssh_host_fingerprint: str | None = None
    has_ssh_password: bool = False
    distance_m: float | None = None

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "PtpLiteBeamRead":
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "ssh_password"):
            instance.has_ssh_password = bool(obj.ssh_password)
        return instance


class ClientModemRead(_DeviceBaseRead):
    device_type: Literal["client_modem"] = "client_modem"
    lr_id: int | None = None
    management_protocol: str = "ssh"
    management_port: int = 22
    management_username: str | None = None
    management_host_fingerprint: str | None = None
    has_management_password: bool = False

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "ClientModemRead":
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "management_password"):
            instance.has_management_password = bool(obj.management_password)
        return instance


# Discriminated union for responses — FastAPI emits the type matching device_type.
DeviceRead = (
    RocketRead | LrRead | UispPowerRead | UispSwitchRead | ClientModemRead
    | AirFiberRead | PtpLiteBeamRead
)
