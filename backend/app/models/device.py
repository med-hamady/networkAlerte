import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Device(Base):
    """Network device being monitored."""

    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, unique=True)
    device_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    location: Mapped[str | None] = mapped_column(String(255))
    snmp_community: Mapped[str | None] = mapped_column(String(100))
    ssh_username: Mapped[str | None] = mapped_column(String(100))
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=22)
    # Pinned SSH host key fingerprint (e.g. "SHA256:abc..."). Recorded on
    # first successful connect (TOFU). Subsequent connects refuse to
    # authenticate if the device returns a different key — defends against
    # MITM on the LAN segment between supervisor and the LTU LR.
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Per-device UISP Power API credentials. When non-null, override the global
    # UISP_POWER_* env vars. Useful when several UISP Power units have distinct
    # passwords without polluting .env.
    uisp_power_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uisp_power_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uisp_power_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Auto-discovery fields. Populated when a peer is reported by a parent
    # Rocket: mac_address is the stable identifier (survives IP changes and
    # Rocket reassignment), auto_discovered distinguishes operator-created
    # devices from those created automatically.
    mac_address: Mapped[str | None] = mapped_column(String(17), unique=True, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    auto_discovered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-device alert_policy overrides — see services/alert_policy.merge_overrides.
    # Shape: {alert_type: {channels?, notify_immediately?, recovery_notification?, ...}}
    policy_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Hierarchy: an LTU LR is attached to a parent LTU Rocket
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    parent: Mapped["Device | None"] = relationship(
        "Device", remote_side="Device.id", back_populates="children",
        foreign_keys=[parent_id], lazy="selectin",
    )
    children: Mapped[list["Device"]] = relationship(
        "Device", back_populates="parent", foreign_keys=[parent_id], lazy="selectin",
    )

    # Relationships — passive_deletes=True delegates row removal to the DB-level
    # ON DELETE CASCADE on the FK; without it, SQLAlchemy would try to load
    # children and NULL their device_id (which fails — the column is NOT NULL).
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

    def __repr__(self) -> str:
        return f"<Device(id={self.id}, name={self.name!r}, ip={self.ip_address})>"
