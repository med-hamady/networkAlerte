"""network health = moyenne de la disponibilité par site (tous équipements infra)

Nouvelle grandeur « Santé du réseau » : un pourcentage unique affiché en tête de
la section « Pannes par site » du dashboard. Il partage la MÊME fenêtre que les
graphes de pannes (sélecteur Du/Au ; défaut 7 j) — donc calculé par un endpoint
paramétré start/end, pas câblé sur une fenêtre fixe.

Calcul, tel que décrit (« par site, puis moyenne des sites ») :
  - Par ÉQUIPEMENT infra (rocket, switch, UISP Power, AF60, PTP LiteBeam) :
    downtime = somme des épisodes de coupure (fusionnés par gaps-and-islands sur
    merge_gap) clippés à la fenêtre ; dispo = 100 × (1 − downtime / fenêtre),
    bornée [0, 100]. Un équipement jamais down = 100 %.
  - Par SITE : moyenne des disponibilités de ses équipements infra.
  - Santé du réseau : MOYENNE SIMPLE des disponibilités par site (chaque site
    pèse pareil, quel que soit son nombre d'équipements ou de clients).

Seuls les sites ayant au moins un équipement infra sont mesurés (`sites_measured`).

Revision ID: x6a7b8c9d0e1
Revises: w5j6k7l8m9n0
Create Date: 2026-07-27 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "x6a7b8c9d0e1"
down_revision: str | None = "w5j6k7l8m9n0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# MUST stay in sync with AVAILABILITY_ALERT_TYPES (alert_constants) and the other
# outage RPCs (fn_site_outage_summary, fn_dashboard_summary).
_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)
# Infra device types — a down infra device is a site outage. Wider than the
# dashboard `pannes` set (adds ptp_litebeam, a P2P backhaul).
_INFRA = "('rocket','uisp_switch','uisp_power','airfiber','ptp_litebeam')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION fn_network_health(
            p_start timestamptz, p_end timestamptz, p_merge_gap int
        ) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        WITH win AS (
            SELECT GREATEST(1, EXTRACT(EPOCH FROM (p_end - p_start))) AS secs
        ),
        -- Universe: every infra device attached to a site.
        infra_devices AS (
            SELECT id, site
              FROM devices
             WHERE device_type IN {_INFRA} AND site IS NOT NULL
        ),
        base AS (
            SELECT i.device_id, d.site,
                   i.detected_at,
                   COALESCE(i.resolved_at, now()) AS real_end
              FROM incidents i
              JOIN devices d ON d.id = i.device_id
             WHERE i.alert_type IN {_AVAIL}
               AND d.device_type IN {_INFRA}
               AND i.detected_at <= p_end
               AND (i.resolved_at IS NULL OR i.resolved_at >= p_start)
        ),
        flagged AS (
            SELECT base.*,
                   CASE WHEN lag(real_end) OVER w IS NULL
                          OR detected_at - lag(real_end) OVER w
                             >= make_interval(secs => p_merge_gap)
                        THEN 1 ELSE 0 END AS is_new
              FROM base
            WINDOW w AS (PARTITION BY device_id ORDER BY detected_at)
        ),
        grouped AS (
            SELECT flagged.*,
                   sum(is_new) OVER (PARTITION BY device_id ORDER BY detected_at) AS grp
              FROM flagged
        ),
        episodes AS (
            SELECT device_id, grp,
                   min(detected_at) AS ep_start,
                   max(real_end)    AS ep_end
              FROM grouped
             GROUP BY device_id, grp
        ),
        per_device_down AS (
            SELECT device_id,
                   sum(GREATEST(0, EXTRACT(EPOCH FROM
                       (LEAST(ep_end, p_end) - GREATEST(ep_start, p_start))))
                   ) AS downtime_seconds
              FROM episodes
             GROUP BY device_id
        ),
        per_device AS (
            SELECT idv.site,
                   GREATEST(0, LEAST(100,
                       100 * (1 - COALESCE(pdd.downtime_seconds, 0) / w.secs))
                   ) AS availability_pct
              FROM infra_devices idv
              CROSS JOIN win w
              LEFT JOIN per_device_down pdd ON pdd.device_id = idv.id
        ),
        per_site AS (
            SELECT site, avg(availability_pct) AS availability_pct
              FROM per_device
             GROUP BY site
        )
        SELECT jsonb_build_object(
            'network_health_pct',
                COALESCE(round(avg(availability_pct)::numeric, 2), 100),
            'sites_measured', count(*),
            'window_start',   p_start,
            'window_end',     p_end,
            'sites', COALESCE(jsonb_agg(jsonb_build_object(
                         'site', site,
                         'availability_pct', round(availability_pct::numeric, 2)
                     ) ORDER BY availability_pct ASC), '[]'::jsonb)
        )
        FROM per_site
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS fn_network_health(timestamptz, timestamptz, int)"
    )
