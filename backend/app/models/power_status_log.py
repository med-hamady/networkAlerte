import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PowerStatusLog(Base):
    """Power status reading from a UISP Power device."""

    __tablename__ = "power_status_logs"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    voltage: Mapped[float | None] = mapped_column(Float)
    current: Mapped[float | None] = mapped_column(Float)
    power: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    device: Mapped["Device"] = relationship(back_populates="power_logs")  # noqa: F821

    def __repr__(self) -> str:
        return f"<PowerStatusLog(device_id={self.device_id}, power={self.power}, status={self.status})>"
