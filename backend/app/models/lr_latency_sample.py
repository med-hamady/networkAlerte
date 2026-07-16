import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LrLatencySample(Base):
    """LR → Internet RTT history, aggregated into fixed 5-minute buckets.

    Feeds the latency graph of the device modal (``GET /devices/{id}/latency-history``).

    Why a dedicated table rather than ``device_metrics``: the probe measures every
    60 s on every LR (~600), so appending raw samples would add ~860k rows/day and
    re-create the device_metrics bloat we already fought once. Instead
    ``lr_internet_probe_job`` upserts the *current* bucket on each cycle, folding
    the new reading into a running average (see ``lr_latency_history_service.
    record_sample``). That is one row per (LR, 5 min) → ~5.2M rows at the 30-day
    retention, purged by ``lr_latency_retention_job``.

    ``min_ms``/``max_ms`` exist because a 5-minute mean hides exactly what the
    graph is for: a 2-minute spike to 400 ms — the thing a client actually calls
    about — would be averaged away. Keeping the extremes lets the chart draw a
    min/max band around the mean, so the spike stays visible.

    A bucket with no row means "no measurement": the probe deliberately records
    nothing when the LR has no transit (the RTT would be meaningless), so the
    chart shows a gap there rather than a misleading zero.

    ``device_metrics.lr_latency_ms`` is untouched by all this — it stays
    collapsed (latest-only) and remains the source for the /lr-health pages.
    """

    __tablename__ = "lr_latency_samples"

    __table_args__ = (
        # One row per (LR, bucket): the target of the upsert's ON CONFLICT, and
        # the index that serves the chart's range scan (device_id + time window).
        UniqueConstraint("device_id", "bucket_start", name="uq_lr_latency_device_bucket"),
        # Retention purges on bucket_start alone, across all devices.
        Index("ix_lr_latency_samples_bucket", "bucket_start"),
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
    )
    bucket_start: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    avg_ms: Mapped[float] = mapped_column(Float, nullable=False)
    min_ms: Mapped[float] = mapped_column(Float, nullable=False)
    max_ms: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return (
            f"<LrLatencySample(device_id={self.device_id}, "
            f"bucket={self.bucket_start:%Y-%m-%d %H:%M}, avg={self.avg_ms:.1f}ms, "
            f"n={self.sample_count})>"
        )
