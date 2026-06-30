import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrafficDestStat(Base):
    """Aggregated client→Internet traffic per (time bucket, destination ASN).

    Populated by the NetFlow collector (``app/services/netflow_service.py``),
    which folds every flow's destination IP into its ASN (MaxMind GeoLite2-ASN)
    and flushes one row per ``(bucket_start, asn)`` every
    ``netflow_flush_interval_seconds``. ``asn`` NULL = a public destination whose
    ASN could not be resolved (mmdb miss). The ``/traffic`` page sums ``bytes``
    over a period and groups by ``asn``/``as_org`` to rank operators/CDNs — the
    signal used to decide which cache (GGC/FNA/OCA) to request.

    Kept small by aggregation; old buckets are purged by
    ``traffic_stats_retention_job`` (batched, like device_metrics).
    """

    __tablename__ = "traffic_dest_stats"

    # Range scan on bucket_start serves both the period roll-up and the
    # retention purge (`WHERE bucket_start < cutoff`).
    __table_args__ = (
        Index("ix_traffic_dest_stats_bucket", "bucket_start"),
    )

    bucket_start: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    asn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    as_org: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    packets: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    flows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return (
            f"<TrafficDestStat(bucket={self.bucket_start:%Y-%m-%d %H:%M}, "
            f"asn={self.asn}, bytes={self.bytes})>"
        )
