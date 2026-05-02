import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Incident(Base):
    """Detected incident on a monitored device."""

    __tablename__ = "incidents"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="info")  # info, warning, critical
    status: Mapped[str] = mapped_column(String(20), default="open")  # open, acknowledged, resolved
    detected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Alert engine fields — populated when incident is raised via the alert engine
    alert_type: Mapped[str | None] = mapped_column(String(50))          # stable key: "signal_low"
    metric_name: Mapped[str | None] = mapped_column(String(100))        # "signal_dbm"
    metric_value: Mapped[float | None] = mapped_column(Float)           # measured value
    threshold_value: Mapped[float | None] = mapped_column(Float)        # crossed threshold
    probable_cause: Mapped[str | None] = mapped_column(String(100))     # correlation result
    last_triggered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Digest batching: marker timestamp set when this incident has been
    # included in a warning digest send. NULL means not yet digested.
    digested_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    device: Mapped["Device"] = relationship(back_populates="incidents")  # noqa: F821
    alerts: Mapped[list["Alert"]] = relationship(back_populates="incident")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Incident(id={self.id}, title={self.title!r}, severity={self.severity})>"
