"""Per-device metric history for the device-modal charts: bucketed writes + reads.

Write side: :func:`record_sample` is called from ``persist_device_metrics`` — the
chokepoint every polling job already goes through — for the metrics in
:data:`GRAPH_METRICS`. It folds the reading into the current time bucket
(upsert + running average, width = `lr_metric_history_bucket_seconds`) instead of
appending a row per poll unbounded.

Read side: :func:`get_history` serves the charts, re-binning wide windows so a
30-day request returns ~360 points rather than 8640.
"""
import datetime
import logging

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.lr_metric_sample import LrMetricSample

logger = logging.getLogger(__name__)

# Metrics mirrored into the history table, i.e. the curves the device modal can
# draw. Deliberately a SHORT allowlist, not "everything a poll reports": each
# entry costs ~288 rows/device/day, so a device reporting 25 metrics would bloat
# the table for curves nobody looks at. Adding one here is all it takes to make
# it graphable — no migration, no new table.
#
# `unit` and `label` live here so the API can describe a curve without the
# frontend hard-coding it; `zero_based` says whether the Y axis must start at 0
# (true for a magnitude like Mb/s or ms — starting elsewhere would exaggerate
# small variations into cliffs).
GRAPH_METRICS: dict[str, dict] = {
    "lr_latency_ms": {
        "label": "Latence Internet",
        "unit": "ms",
        "zero_based": True,
        # Lower is better → the alert threshold is an upper bound.
        "threshold_setting": "lr_latency_critical_ms",
        "threshold_direction": "max",
    },
    "total_capacity_mbps": {
        "label": "Capacité du lien",
        "unit": "Mb/s",
        "zero_based": True,
        # Higher is better → the alert floor is a lower bound.
        "threshold_setting": "lr_total_capacity_min_mbps",
        "threshold_direction": "min",
    },
    "link_potential_pct": {
        "label": "Potentiel du lien",
        "unit": "%",
        "zero_based": True,
        # Plancher PAR FAMILLE : les deux radios n'ont pas le même rendement, et
        # l'alerting le sait déjà (50 % en LTU, 40 % en airMAX). Un seuil unique
        # peindrait en rouge un LiteBeam parfaitement sain à 45 %.
        "threshold_setting": {
            "ltu": "lr_link_potential_min_pct_ltu",
            "airmax": "lr_link_potential_min_pct_airmax",
        },
        "threshold_direction": "min",
    },
    "tx_rate_mbps": {
        "label": "Débit descendant du lien",
        "unit": "Mb/s",
        "zero_based": True,
        "threshold_setting": None,
        "threshold_direction": None,
    },
    "rx_rate_mbps": {
        "label": "Débit montant du lien",
        "unit": "Mb/s",
        "zero_based": True,
        "threshold_setting": None,
        "threshold_direction": None,
    },
}


def threshold_setting_for(spec: dict, device) -> str | None:
    """Nom du réglage de seuil applicable à ``device`` pour cette métrique.

    ``threshold_setting`` vaut None (pas de seuil), une chaîne (seuil unique), ou
    un dict par famille radio. Ce dernier cas existe pour le potentiel du lien,
    dont le plancher dépend de la famille.

    La famille est résolue avec la définition de ``alert_rules`` — importée, pas
    recopiée : la ligne tracée doit être exactement celle qui déclenche l'alerte,
    et deux listes de variantes qui divergent donneraient un graphe qui ment.
    """
    setting = spec["threshold_setting"]
    if setting is None or isinstance(setting, str):
        return setting

    from app.services.alert_rules import _AIRMAX_LR_VARIANTS

    variant = getattr(device, "model_variant", None)
    family = "airmax" if variant in _AIRMAX_LR_VARIANTS else "ltu"
    return setting[family]

# Largeur d'un bucket stocké, en secondes (env `LR_METRIC_HISTORY_BUCKET_SECONDS`).
#
# 60 s = la cadence des polls (SNMP/airOS/LTU), donc chaque relevé a son propre
# point : c'est la résolution maximale que les données permettent. Le bucket
# n'est PAS le facteur limitant pour la latence — sa sonde SSH tourne toutes les
# 3 min (`lr_latency_interval`) et un tour dure 100-480 s sur ~800 LR, donc un
# client produit une mesure toutes les 3-8 min quoi qu'on fasse ici. Ses buckets
# resteront à ~1 mesure ; ceux des métriques de poll en auront une par minute.
#
# Coût : ~3× le volume d'un bucket 5 min (~110 M lignes à 30 j de rétention).
# Assumé — surveiller l'autovacuum, cf. l'épisode de bloat de device_metrics.
#
# Changer cette valeur NE réécrit PAS les lignes existantes : les anciens buckets
# gardent leur largeur d'origine (une série peut donc mélanger deux résolutions à
# la charnière du déploiement, ce qui est visuellement inoffensif).
def bucket_seconds() -> int:
    return get_settings().lr_metric_history_bucket_seconds

# Fenêtres proposées par les graphes, et la largeur de bin utilisée pour chacune.
# 24h sort à la résolution native (1440 points à 60 s) ; au-delà on re-binne côté
# serveur pour garder un payload et un tracé SVG raisonnables :
#   7d  → bins 30 min → 336 points
#   30d → bins 2 h    → 360 points
_PERIODS: dict[str, tuple[datetime.timedelta, int]] = {
    # 24h : résolution NATIVE (pas de re-binning) — c'est la fenêtre de
    # diagnostic, on y veut chaque relevé. 0 = "prendre bucket_seconds()",
    # résolu à l'appel : la largeur est un réglage, pas une constante.
    "24h": (datetime.timedelta(hours=24), 0),
    "7d": (datetime.timedelta(days=7), 1800),
    "30d": (datetime.timedelta(days=30), 7200),
}


def _bin_seconds_for_span(span: datetime.timedelta) -> int:
    """Bin width for an arbitrary custom range, picked from its span so the point
    count stays in the same ballpark as the presets."""
    hours = span.total_seconds() / 3600
    if hours <= 24:
        return bucket_seconds()
    if hours <= 24 * 7:
        return 1800
    if hours <= 24 * 30:
        return 7200
    return 21600  # 6 h — beyond the retention window, but keeps the query bounded


def floor_bucket(
    moment: datetime.datetime, *, width_seconds: int | None = None
) -> datetime.datetime:
    """Floor a timestamp to the start of its bucket (UTC, epoch-aligned).

    Epoch-aligned so every device lands on the same bucket boundaries regardless
    of when its poll cycle happens to fire — matching the date_bin() origin used
    on the read side.
    """
    width = width_seconds or bucket_seconds()
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    elapsed = int((moment - epoch).total_seconds())
    return epoch + datetime.timedelta(seconds=elapsed - (elapsed % width))


async def record_sample(
    session: AsyncSession,
    device_id: int,
    metric_name: str,
    value: float,
    *,
    now: datetime.datetime | None = None,
) -> None:
    """Fold one reading into its time bucket. Caller owns the transaction.

    First reading of a bucket inserts it; later ones update in place, recomputing
    the mean incrementally as ``(avg*n + value) / (n+1)`` and widening min/max.
    The arithmetic runs in SQL against the existing row, so concurrent writers on
    the same device can't lose an update the way a read-modify-write in Python
    would.
    """
    moment = now or datetime.datetime.now(datetime.UTC)
    bucket = floor_bucket(moment)
    val = float(value)

    stmt = pg_insert(LrMetricSample).values(
        device_id=device_id,
        metric_name=metric_name,
        bucket_start=bucket,
        avg_value=val,
        min_value=val,
        max_value=val,
        sample_count=1,
        created_at=moment,
        updated_at=moment,
    )
    n = LrMetricSample.sample_count
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_lr_metric_device_name_bucket",
            set_={
                "avg_value": (LrMetricSample.avg_value * n + val) / (n + 1),
                "min_value": func.least(LrMetricSample.min_value, val),
                "max_value": func.greatest(LrMetricSample.max_value, val),
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
    return now - window, now, step or bucket_seconds()


async def get_history(
    db: AsyncSession,
    device_id: int,
    metric_name: str,
    *,
    start: datetime.datetime,
    end: datetime.datetime,
    bin_seconds: int,
) -> list[dict]:
    """Return one metric's series for one device over [start, end), oldest first.

    Each point carries ``avg_value`` (mean), ``min_value``/``max_value`` (the band,
    so a short spike swallowed by the mean stays visible) and ``sample_count``.

    Gaps are NOT filled: a bucket with no row is simply absent. That is the honest
    rendering — nothing was measured then (device unreachable, or an LR with no
    transit), and emitting a 0 there would read as "0 ms" / "0 Mb/s".

    When ``bin_seconds`` > the stored bucket width, rows are re-aggregated: the
    mean is weighted by ``sample_count`` (a plain AVG of averages would over-weight
    a bucket that only got one reading), while min/max stay true extremes.
    """
    params = {
        "device_id": device_id, "metric_name": metric_name,
        "start": start, "end": end,
    }
    if bin_seconds <= bucket_seconds():
        rows = (
            await db.execute(
                text(
                    "SELECT bucket_start, avg_value, min_value, max_value, sample_count "
                    "FROM lr_metric_samples "
                    "WHERE device_id = :device_id AND metric_name = :metric_name "
                    "  AND bucket_start >= :start AND bucket_start < :end "
                    "ORDER BY bucket_start"
                ),
                params,
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
                    "       SUM(avg_value * sample_count) / SUM(sample_count) AS avg_value, "
                    "       MIN(min_value) AS min_value, "
                    "       MAX(max_value) AS max_value, "
                    "       SUM(sample_count) AS sample_count "
                    "FROM lr_metric_samples "
                    "WHERE device_id = :device_id AND metric_name = :metric_name "
                    "  AND bucket_start >= :start AND bucket_start < :end "
                    "GROUP BY t ORDER BY t"
                ),
                params,
            )
        ).all()

    # Index access: attribute access on a text() Row is unreliable (`.t` is taken
    # by SQLAlchemy 2.0). Columns: (bucket_start, avg, min, max, count).
    return [
        {
            "bucket_start": r[0],
            "avg_value": round(float(r[1]), 2),
            "min_value": round(float(r[2]), 2),
            "max_value": round(float(r[3]), 2),
            "sample_count": int(r[4]),
        }
        for r in rows
    ]


async def available_metrics(db: AsyncSession, device_id: int) -> list[dict]:
    """Which GRAPH_METRICS this device actually has history for, with their labels.

    Lets the modal show only the curves that exist: an LTU LR and a LiteBeam do
    not report the same set, and offering an empty tab reads as a bug.

    Labels ride along so the modal can render its tabs without re-deriving them —
    GRAPH_METRICS stays the single source of truth for what a curve is called.
    """
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT metric_name FROM lr_metric_samples "
                "WHERE device_id = :device_id"
            ),
            {"device_id": device_id},
        )
    ).all()
    present = {r[0] for r in rows}
    # Keep GRAPH_METRICS' order — it is the display order in the modal.
    return [
        {"name": name, "label": spec["label"], "unit": spec["unit"]}
        for name, spec in GRAPH_METRICS.items()
        if name in present
    ]
