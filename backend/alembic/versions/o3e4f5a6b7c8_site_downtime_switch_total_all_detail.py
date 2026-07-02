"""site downtime: total = switch, drill-down = every infra device

Suite de n2d3e4f5a6b7. Le total « Temps de panne par site » reste le downtime du
switch parent, MAIS le drill-down (clic sur un site) doit à nouveau lister
CHAQUE équipement infra avec son propre temps de coupure (comme avant le
passage switch-only), pas seulement le switch.

Seul change, vs n2d3e4f5a6b7, la source de la liste `devices` de `by_downtime` :
`switch_site.devices` (switch seul) → `per_site.devices` (tout l'infra). Le total
`downtime_seconds` reste `switch_site` (switch seul) et le filtrage « site sans
switch → masqué » est préservé par le JOIN sur switch_site.

Revision ID: o3e4f5a6b7c8
Revises: n2d3e4f5a6b7
Create Date: 2026-07-02 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "o3e4f5a6b7c8"
down_revision: str | None = "n2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)
_INFRA = "('rocket','uisp_switch','uisp_power','airfiber','ptp_litebeam')"


def _create(all_infra_detail: bool) -> None:
    """(Re)create fn_site_outage_summary.

    `by_downtime` total = switch-only downtime in both variants. When
    `all_infra_detail` is True the drill-down `devices` list holds every infra
    device (new behaviour); when False it holds the switch(es) only (the
    n2d3e4f5a6b7 behaviour, for downgrade).
    """
    op.execute(
        "DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)"
    )

    downtime_devices = "ps.devices" if all_infra_detail else "sw.devices"

    op.execute(
        f"""
        CREATE FUNCTION fn_site_outage_summary(
            p_start timestamptz, p_end timestamptz, p_merge_gap int
        ) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        WITH base AS (
            SELECT i.device_id, d.site, d.name AS device_name,
                   d.device_type, d.status AS current_status,
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
            SELECT device_id, site, device_name, device_type, current_status, grp,
                   min(detected_at) AS ep_start,
                   max(real_end)    AS ep_end
              FROM grouped
             GROUP BY device_id, site, device_name, device_type, current_status, grp
        ),
        per_device AS (
            SELECT device_id, site, device_name, device_type, current_status,
                   count(*) AS episodes_count,
                   sum(GREATEST(0, EXTRACT(EPOCH FROM
                       (LEAST(ep_end, p_end) - GREATEST(ep_start, p_start))))
                   ) AS total_downtime_seconds
              FROM episodes
             GROUP BY device_id, site, device_name, device_type, current_status
        ),
        per_site AS (
            SELECT site,
                   sum(episodes_count) AS pannes,
                   sum(total_downtime_seconds) AS downtime_seconds,
                   jsonb_agg(jsonb_build_object(
                       'device_id', device_id, 'device_name', device_name,
                       'device_type', device_type, 'current_status', current_status,
                       'episodes_count', episodes_count,
                       'total_downtime_seconds', total_downtime_seconds
                   ) ORDER BY total_downtime_seconds DESC) AS devices
              FROM per_device
             GROUP BY site
        ),
        switch_site AS (
            SELECT site,
                   sum(total_downtime_seconds) AS downtime_seconds,
                   jsonb_agg(jsonb_build_object(
                       'device_id', device_id, 'device_name', device_name,
                       'device_type', device_type, 'current_status', current_status,
                       'episodes_count', episodes_count,
                       'total_downtime_seconds', total_downtime_seconds
                   ) ORDER BY total_downtime_seconds DESC) AS devices
              FROM per_device
             WHERE device_type = 'uisp_switch'
             GROUP BY site
        ),
        per_site_downtime AS (
            SELECT ps.site,
                   ps.pannes,
                   sw.downtime_seconds,
                   {downtime_devices} AS devices
              FROM per_site ps
              JOIN switch_site sw ON sw.site = ps.site
        )
        SELECT jsonb_build_object(
            'by_pannes', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices
                       ) ORDER BY pannes DESC)
                  FROM per_site WHERE pannes > 0
            ), '[]'::jsonb),
            'by_downtime', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices
                       ) ORDER BY downtime_seconds DESC)
                  FROM per_site_downtime WHERE downtime_seconds > 0
            ), '[]'::jsonb)
        )
        $$
        """
    )


def upgrade() -> None:
    _create(all_infra_detail=True)


def downgrade() -> None:
    _create(all_infra_detail=False)
