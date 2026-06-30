import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrafficDestStat(Base):
    """Aggregated client↔Internet traffic per (time bucket, operator ASN).

    Populated by the NetFlow collector (``app/services/netflow_service.py``).
    Each flow is attributed to its **public endpoint** (the Internet operator/CDN)
    — the destination for upload flows, the source for download flows — resolved
    to its ASN (MaxMind GeoLite2-ASN). Bytes are split by direction:

      - ``down_bytes`` : descending / download — operator → client (RX on the WAN),
        the bandwidth that matters for cache decisions (GGC/FNA/OCA).
      - ``up_bytes``   : ascending / upload — client → operator (TX on the WAN).

    One row per ``(bucket_start, asn)`` flushed every
    ``netflow_flush_interval_seconds``. ``asn`` NULL = public endpoint whose ASN
    could not be resolved (mmdb miss). The ``/traffic`` page sums bytes over a
    period (volume) and derives throughput (bytes ÷ bucket seconds → Gb/s) to
    rank operators/CDNs and show how the WAN bandwidth is shared.

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
    down_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    up_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    flows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return (
            f"<TrafficDestStat(bucket={self.bucket_start:%Y-%m-%d %H:%M}, "
            f"asn={self.asn}, down={self.down_bytes}, up={self.up_bytes})>"
        )
