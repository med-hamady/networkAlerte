"""Durable execution history, independent of the customer's current state."""

import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FaiRequest(Base):
    __tablename__ = "fai_requests"
    __table_args__ = (Index("ix_fai_requests_device_active", "device_id", "active"),)

    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True,
    )
    request_key: Mapped[str] = mapped_column(String(36), unique=True)
    mac: Mapped[str | None] = mapped_column(String(17))
    name: Mapped[str] = mapped_column(String(255))
    site: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(120))
    agent: Mapped[str | None] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    identification: Mapped[dict] = mapped_column(JSON, default=dict)
    # One interval per observed wait. Its end is the return observation, not
    # completion of the later network operation.
    waiting_periods: Mapped[list] = mapped_column(JSON, default=list)
    received_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(Text)
    # Latest result per required network action; detailed attempts live in events.
    actions: Mapped[list] = mapped_column(JSON, default=list)


class FaiRequestEvent(Base):
    __tablename__ = "fai_request_events"
    __table_args__ = (Index("ix_fai_request_events_request_time", "request_id", "timestamp"),)

    request_id: Mapped[int] = mapped_column(ForeignKey("fai_requests.id", ondelete="CASCADE"))
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    target: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30))
    result: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
