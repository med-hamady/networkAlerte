import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AlertState(Base):
    """Persisted failure counter for alert engine anti-flapping.

    One row per (device, alert_type) pair. Survives container restarts.
    """

    __tablename__ = "alert_states"
    __table_args__ = (UniqueConstraint("device_id", "alert_type", name="uq_alert_state"),)

    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Last raw metric value — used by delta-based rules (e.g. error counters)
    last_metric_value: Mapped[float | None] = mapped_column(Float)
    last_evaluated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    device: Mapped["Device"] = relationship(back_populates="alert_states")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<AlertState(device_id={self.device_id}, "
            f"alert_type={self.alert_type!r}, count={self.failure_count})>"
        )
