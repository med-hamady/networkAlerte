import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LrMetricSample(Base):
    """Per-device metric history, aggregated into fixed 5-minute buckets.

    Feeds the charts of the device modal (``GET /devices/{id}/metric-history``):
    latency, link capacity, link rates — one curve per ``metric_name``.

    Why a dedicated table rather than ``device_metrics``: the polls run every
    30-60 s over ~800 LRs, so appending raw samples would add ~1M rows/day and
    re-create the device_metrics bloat we already fought once. Instead
    ``persist_device_metrics`` folds each reading into the *current* bucket via
    upsert, keeping a running average (see ``lr_metric_history_service.
    record_sample``). That is one row per (device, metric, 5 min).

    ``min_value``/``max_value`` exist because a 5-minute mean hides exactly what
    the charts are for: a 2-minute latency spike or a capacity dip — the thing a
    client actually calls about — would be averaged away. Keeping the extremes
    lets each chart draw a min/max band around the mean.

    A bucket with no row means "no measurement": the probe records nothing when
    an LR has no transit, and a poll records nothing when the device is
    unreachable. The charts draw that as a gap rather than a misleading zero.

    ``device_metrics`` is untouched by all this — the metrics mirrored here stay
    collapsed (latest-only) there, which is what the /lr-health pages read.
    """

    __tablename__ = "lr_metric_samples"

    __table_args__ = (
        # One row per (device, metric, bucket): the target of the upsert's
        # ON CONFLICT, and the index serving each chart's range scan
        # (device_id + metric_name + time window).
        UniqueConstraint(
            "device_id", "metric_name", "bucket_start",
            name="uq_lr_metric_device_name_bucket",
        ),
        # Retention purges on bucket_start alone, across all devices/metrics —
        # the unique constraint above leads with device_id so it can't serve it.
        Index("ix_lr_metric_samples_bucket", "bucket_start"),
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    bucket_start: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    avg_value: Mapped[float] = mapped_column(Float, nullable=False)
    min_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return (
            f"<LrMetricSample(device_id={self.device_id}, {self.metric_name}, "
            f"bucket={self.bucket_start:%Y-%m-%d %H:%M}, avg={self.avg_value:.1f}, "
            f"n={self.sample_count})>"
        )
