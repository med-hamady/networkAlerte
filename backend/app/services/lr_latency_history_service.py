"""LR → Internet latency history: bucketed writes + chart reads.

Write side: :func:`record_sample` is called by ``lr_internet_probe_job`` on every
cycle that measured an RTT. It folds the reading into the current 5-minute bucket
(upsert + running average) instead of appending a row per probe, keeping the table
at ~5.2M rows for 30 days of ~600 LRs.

Read side: :func:`get_history` serves the device modal's chart, re-binning wide
windows so a 30-day request returns ~360 points rather than 8640.
"""
import datetime
import logging

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lr_latency_sample import LrLatencySample

logger = logging.getLogger(__name__)

# Width of a stored bucket. Changing this does NOT rewrite existing rows: old
# buckets keep their original width, so only touch it with a backfill in mind.
BUCKET_SECONDS = 300  # 5 min

# Relative windows offered by the chart, and the bin width used to render each.
# 5-min rows are returned as-is over 24h (288 points); wider windows are re-binned
# server-side to keep the payload and the SVG path a sane size.
#   7d  → 30-min bins → 336 points
#   30d → 2-h bins    → 360 points
_PERIODS: dict[str, tuple[datetime.timedelta, int]] = {
    "24h": (datetime.timedelta(hours=24), BUCKET_SECONDS),
    "7d": (datetime.timedelta(days=7), 1800),
    "30d": (datetime.timedelta(days=30), 7200),
}

# Bin width applied to an arbitrary custom [start, end) range, picked from its
# span so the point count stays in the same ballpark as the presets.
def _bin_seconds_for_span(span: datetime.timedelta) -> int:
    hours = span.total_seconds() / 3600
    if hours <= 24:
        return BUCKET_SECONDS
    if hours <= 24 * 7:
        return 1800
    if hours <= 24 * 30:
        return 7200
    return 21600  # 6 h — beyond the retention window, but keeps the query bounded


def floor_bucket(
    moment: datetime.datetime, *, width_seconds: int = BUCKET_SECONDS
) -> datetime.datetime:
    """Floor a timestamp to the start of its bucket (UTC, epoch-aligned).

    Epoch-aligned so every LR lands on the same bucket boundaries regardless of
    when its probe cycle happens to fire — matching the date_bin() origin used on
    the read side.
    """
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    elapsed = int((moment - epoch).total_seconds())
    return epoch + datetime.timedelta(seconds=elapsed - (elapsed % width_seconds))


async def record_sample(
    session: AsyncSession,
    device_id: int,
    rtt_ms: float,
    *,
    now: datetime.datetime | None = None,
) -> None:
    """Fold one RTT reading into its 5-minute bucket. Caller owns the transaction.

    First reading of a bucket inserts it; later ones update in place, recomputing
    the mean incrementally as ``(avg*n + rtt) / (n+1)`` and widening min/max. The
    arithmetic runs in SQL against the existing row, so concurrent probes on the
    same LR can't lose an update the way a read-modify-write in Python would.
    """
    moment = now or datetime.datetime.now(datetime.UTC)
    bucket = floor_bucket(moment)

    stmt = pg_insert(LrLatencySample).values(
        device_id=device_id,
        bucket_start=bucket,
        avg_ms=rtt_ms,
        min_ms=rtt_ms,
        max_ms=rtt_ms,
        sample_count=1,
        created_at=moment,
        updated_at=moment,
    )
    n = LrLatencySample.sample_count
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_lr_latency_device_bucket",
            set_={
                "avg_ms": (LrLatencySample.avg_ms * n + rtt_ms) / (n + 1),
                "min_ms": func.least(LrLatencySample.min_ms, rtt_ms),
                "max_ms": func.greatest(LrLatencySample.max_ms, rtt_ms),
                "sample_count": n + 1,
                "updated_at": moment,
            },
        )
    )


def resolve_range(
    period: str | None,
    start: datetime.date | None,
    end: datetime.date | None,
) -> tuple[datetime.datetime, datetime.datetime, int]:
    """Resolve the request into (start, end, bin_seconds).

    A ``start``/``end`` date range wins over ``period``; an unknown or missing
    period falls back to 24h. Dates are UTC days and ``end`` is **inclusive** —
    same convention as /clients/consumption, so an operator picking the same two
    days on both pages gets the same window rather than losing the last day here.
    """
    now = datetime.datetime.now(datetime.UTC)

    if start is not None and end is not None:
        range_start = datetime.datetime.combine(start, datetime.time.min, datetime.UTC)
        # Inclusive end day → exclusive upper bound at the start of the next day.
        range_end = datetime.datetime.combine(
            end + datetime.timedelta(days=1), datetime.time.min, datetime.UTC,
        )
        return range_start, range_end, _bin_seconds_for_span(range_end - range_start)

    window, step = _PERIODS.get(period or "24h", _PERIODS["24h"])
    return now - window, now, step


async def get_history(
    db: AsyncSession,
    device_id: int,
    *,
    start: datetime.datetime,
    end: datetime.datetime,
    bin_seconds: int,
) -> list[dict]:
    """Return the latency series of one LR over [start, end), oldest first.

    Each point carries ``avg_ms`` (mean), ``min_ms``/``max_ms`` (the band, so a
    short spike swallowed by the mean stays visible) and ``sample_count``.

    Gaps are NOT filled: a bucket with no row is simply absent. That is the honest
    rendering — the probe records nothing when the LR has no transit or wasn't
    reachable over SSH, and emitting a 0 there would read as "0 ms latency".

    When ``bin_seconds`` > the stored bucket width, rows are re-aggregated: the
    mean is weighted by ``sample_count`` (a plain AVG of averages would over-weight
    a bucket that only got one reading), while min/max stay true extremes.
    """
    if bin_seconds <= BUCKET_SECONDS:
        rows = (
            await db.execute(
                text(
                    "SELECT bucket_start, avg_ms, min_ms, max_ms, sample_count "
                    "FROM lr_latency_samples "
                    "WHERE device_id = :device_id "
                    "  AND bucket_start >= :start AND bucket_start < :end "
                    "ORDER BY bucket_start"
                ),
                {"device_id": device_id, "start": start, "end": end},
            )
        ).all()
    else:
        # bin_seconds is a trusted int from _PERIODS/_bin_seconds_for_span → safe
        # to inline. Passing it as a bound param makes asyncpg expect a timedelta.
        rows = (
            await db.execute(
                text(
                    f"SELECT date_bin('{bin_seconds} seconds', bucket_start, "
                    "                 TIMESTAMPTZ 'epoch') AS t, "
                    "       SUM(avg_ms * sample_count) / SUM(sample_count) AS avg_ms, "
                    "       MIN(min_ms) AS min_ms, "
                    "       MAX(max_ms) AS max_ms, "
                    "       SUM(sample_count) AS sample_count "
                    "FROM lr_latency_samples "
                    "WHERE device_id = :device_id "
                    "  AND bucket_start >= :start AND bucket_start < :end "
                    "GROUP BY t ORDER BY t"
                ),
                {"device_id": device_id, "start": start, "end": end},
            )
        ).all()

    # Index access: attribute access on a text() Row is unreliable (`.t` is taken
    # by SQLAlchemy 2.0). Columns: (bucket_start, avg, min, max, count).
    return [
        {
            "bucket_start": r[0],
            "avg_ms": round(float(r[1]), 2),
            "min_ms": round(float(r[2]), 2),
            "max_ms": round(float(r[3]), 2),
            "sample_count": int(r[4]),
        }
        for r in rows
    ]
