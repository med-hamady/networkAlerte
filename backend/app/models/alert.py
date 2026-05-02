import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Alert(Base):
    """Notification sent for an incident via a channel."""

    __tablename__ = "alerts"

    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="SET NULL"),
        nullable=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, sent, failed
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    incident: Mapped["Incident"] = relationship(back_populates="alerts")  # noqa: F821
    channel: Mapped["NotificationChannel | None"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<Alert(id={self.id}, incident_id={self.incident_id}, status={self.status})>"
