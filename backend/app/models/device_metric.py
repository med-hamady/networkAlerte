import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DeviceMetric(Base):
    """Time-series metric collected from a device."""

    __tablename__ = "device_metrics"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(30))
    collected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    device: Mapped["Device"] = relationship(back_populates="metrics")  # noqa: F821

    def __repr__(self) -> str:
        return f"<DeviceMetric(device_id={self.device_id}, {self.metric_name}={self.metric_value})>"
