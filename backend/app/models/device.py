"""
Device hierarchy — joined-table inheritance.

A row in `devices` is the shared identity (id, name, ip, status, SNMP community,
discovery metadata). Each concrete type (Rocket, Lr, UispPower, UispSwitch) lives
in its own table joined by FK on devices.id and carries only the columns that are
meaningful for that type.

The discriminator column `devices.device_type` tells SQLAlchemy which subclass to
instantiate when loading. Use `select(Rocket)` to get rockets only, or
`select(Device)` to get the union (polymorphic load).
"""

import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Device(Base):
    """Shared identity of any monitored device."""

    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Volatile, NOT an identity: DHCP churn moves an IP between MACs over time.
    # Kept UNIQUE (one device per IP at any instant) but nullable so a stale
    # binding can be released (set NULL) when the IP migrates to another device.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, unique=True)
    device_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    location: Mapped[str | None] = mapped_column(String(255))
    snmp_community: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Auto-discovery metadata — populated for devices reported by a peer (LRs)
    # or when an external scan detects a new device. mac_address is the stable
    # identifier across IP changes and Rocket reassignment.
    mac_address: Mapped[str | None] = mapped_column(String(17), unique=True, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    auto_discovered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-device alert_policy overrides — see services/alert_policy.merge_overrides
    policy_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Reverse relationships from dependent tables. passive_deletes=True relies on
    # ON DELETE CASCADE at the FK level — without it, SQLAlchemy would try to
    # load children and NULL their device_id (NOT NULL → integrity error).
    metrics: Mapped[list["DeviceMetric"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )
    incidents: Mapped[list["Incident"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )
    power_logs: Mapped[list["PowerStatusLog"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )
    alert_states: Mapped[list["AlertState"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )

    __mapper_args__ = {
        "polymorphic_on": "device_type",
        "polymorphic_identity": "device",
    }

    def __repr__(self) -> str:
        return f"<{type(self).__name__}(id={self.id}, name={self.name!r}, ip={self.ip_address})>"

    @property
    def rule_category(self) -> str:
        """Coarse-grained category used to pick alert rules and alert types.

        Returns one of: 'ltu_rocket', 'airmax_rocket', 'lr', 'uisp_power',
        'uisp_switch', 'airfiber'. Both LTU and Litebeam subscribers share 'lr'
        for now — split later if variant-specific thresholds are needed.
        """
        if isinstance(self, Rocket):
            return "airmax_rocket" if self.radio_tech == "airmax" else "ltu_rocket"
        if isinstance(self, Lr):
            return "lr"
        # AirFiber + UispSwitch + UispPower : device_type == rule_category.
        return self.device_type


class Rocket(Device):
    """LTU Rocket or airMAX Rocket — a base station radio with an HTTPS API."""

    __tablename__ = "rockets"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # "ltu" for LTU Rockets (LTU LR/Instant/Lite peers), "airmax" for airMAX
    # Rockets (Litebeam peers). Polling routines branch on this.
    radio_tech: Mapped[str] = mapped_column(String(20), nullable=False)

    # HTTPS API credentials (used by ltu_api_service)
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=443)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Reverse: LRs whose parent is this rocket
    lrs: Mapped[list["Lr"]] = relationship(
        back_populates="rocket", foreign_keys="Lr.rocket_id", lazy="selectin",
    )

    __mapper_args__ = {"polymorphic_identity": "rocket", "polymorphic_load": "selectin"}


class Lr(Device):
    """Subscriber radio (LR) — connects to a Rocket. Carries the link metrics."""

    __tablename__ = "lrs"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Specific model variant. LTU family: ltu_lr / ltu_instant / ltu_lite.
    # airMAX family: litebeam_5ac / litebeam_m5.
    model_variant: Mapped[str] = mapped_column(String(30), nullable=False)

    # Parent rocket — nullable while an LR has been discovered but not yet
    # associated, or has been orphaned. SET NULL on rocket delete so the LR
    # row survives.
    rocket_id: Mapped[int | None] = mapped_column(
        ForeignKey("rockets.id", ondelete="SET NULL"), nullable=True,
    )
    rocket: Mapped["Rocket | None"] = relationship(
        back_populates="lrs", foreign_keys=[rocket_id], lazy="selectin",
    )

    # SSH credentials (used by the transit probe — LR pings the internet on demand)
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=22)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Link characteristic reported by the parent Rocket's API.
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Client internet block ────────────────────────────────────────────────
    # Cutting a client = SSH into this LR and shutting its LAN-facing port
    # (`lan_interface`, default eth0). SSH itself reaches the LR through the
    # radio link (ath0 → Rocket → supervisor), so the management plane survives
    # the cut. `client_blocked` is the *intent*; `client_block_enforced_at` is
    # the last time the shutdown was actually re-asserted on the device. They
    # can diverge: intent recorded but device unreachable → the enforcement job
    # keeps retrying. NEVER point lan_interface at ath0/br0 — that would lock
    # the supervisor out of the LR. The earlier `devices.is_suspended` flag was
    # a no-op (no enforcement); this pair is the real mechanism.
    client_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false",
    )
    client_blocked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    client_blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lan_interface: Mapped[str] = mapped_column(
        String(20), default="eth0", nullable=False, server_default="eth0",
    )
    client_block_enforced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # How the block is enforced on the LR:
    #   "full"          → shut `lan_interface` (total internet cut).
    #   "whatsapp_only" → iptables allowlist (DNS + Meta/WhatsApp RETURN, rest
    #                     DROP) so the client keeps WhatsApp while the rest of
    #                     the internet is cut. Touches no interface, so it is
    #                     immune to the lock-out trap that `full` must guard.
    # Persisted so the enforcement job re-asserts the right mechanism after a
    # reboot. Default "full" keeps pre-existing blocked LRs unchanged.
    block_mode: Mapped[str] = mapped_column(
        String(20), default="full", nullable=False, server_default="full",
    )
    # Router vs bridge mode — read from each LR's HTTP poll (airMAX: airOS
    # status.cgi host.netrole; LTU: Rocket API peer.remote.netMode), no SSH.
    # The client-block feature only works in router mode (the LR must be in
    # the IP path of the client). In bridge mode (L2-transparent), iptables
    # FORWARD and the local dnsmasq are bypassed; the block endpoint refuses
    # with a clear message and the UI surfaces a misconfig badge.
    # Values: "router" | "bridge" | "unknown" (detection not yet run).
    topology_mode: Mapped[str] = mapped_column(
        String(10), default="unknown", nullable=False, server_default="unknown",
    )

    # ── Subscription plan (forfait) ──────────────────────────────────────────
    # The customer's plan is provisioned on the LR as an airOS traffic shaper
    # (egress rate cap per interface in /tmp/system.cfg), NOT exposed by any
    # HTTP API — `lr_plan_service` reads it over SSH and caches the down/up caps
    # here so the frontend shows "20/10 Mbps" without an SSH round-trip per view.
    # `plan_synced_at` is the last successful read (None = never synced). The
    # commercial plan *name* is not on the device (CRM-only). Both caps None
    # after a sync = the LR has no shaper configured (no forfait on the device).
    plan_download_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    plan_upload_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    plan_synced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __mapper_args__ = {"polymorphic_identity": "lr", "polymorphic_load": "selectin"}


class UispPower(Device):
    """UISP Power — battery-backed PoE PDU with a REST API."""

    __tablename__ = "uisp_powers"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    api_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_port: Mapped[int] = mapped_column(default=443)

    __mapper_args__ = {"polymorphic_identity": "uisp_power", "polymorphic_load": "selectin"}


class UispSwitch(Device):
    """UISP managed switch — monitored via SNMP (no API credentials)."""

    __tablename__ = "uisp_switches"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    max_ports: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    # SNMP ifIndex of the port connected to the supervised Rocket. None = no
    # specific port monitored (we only check the device as a whole).
    rocket_port_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port_min_speed_mbps: Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)

    __mapper_args__ = {"polymorphic_identity": "uisp_switch", "polymorphic_load": "selectin"}


class AirFiber(Device):
    """airFiber 60 (AF60-LR) — lien backhaul point-à-point 60 GHz.

    Équipement d'infrastructure ajouté manuellement (comme UispSwitch / UispPower).
    Parle la MÊME UDAPI que les LTU (POST /api/auth → utoken, GET /api/v1.0/statistics
    avec header x-auth-token) — d'où la réutilisation des creds `ssh_*` comme
    identifiants d'API HTTP, exactement comme Rocket. Lien point-à-point : un seul
    peer (l'autre extrémité), pas d'auto-découverte de CPE.
    """

    __tablename__ = "airfibers"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Identifiants de l'API HTTP locale (consommés par af60_api_service via le
    # client LTU partagé). Même convention de nommage que Rocket.
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=443)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Distance du lien (m), synchronisée depuis l'API pour l'affichage UI.
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    __mapper_args__ = {"polymorphic_identity": "airfiber", "polymorphic_load": "selectin"}


class ClientModem(Device):
    """Customer-side modem (TP-Link, Huawei, ZTE, ...) behind an LR.

    The modem sits in the client LAN behind the LR's NAT, so it's not directly
    reachable from the supervisor. It is inventoried and its reachability is
    probed from the parent LR (the ping-from-LR diagnostic). There is no
    interactive shell — most customer modems expose a web UI only.
    """

    __tablename__ = "client_modems"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Parent LR — provides the SSH jump host. SET NULL on LR delete so the
    # modem row survives orphaned (operator can re-link via PUT).
    lr_id: Mapped[int | None] = mapped_column(
        ForeignKey("lrs.id", ondelete="SET NULL"), nullable=True,
    )
    lr: Mapped["Lr | None"] = relationship(foreign_keys=[lr_id], lazy="selectin")

    # Vestigial inventory metadata — no feature uses these since the
    # interactive shell was removed. Kept to avoid a destructive migration.
    management_protocol: Mapped[str] = mapped_column(String(10), default="ssh", nullable=False)
    management_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    management_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    management_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    management_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __mapper_args__ = {"polymorphic_identity": "client_modem", "polymorphic_load": "selectin"}
