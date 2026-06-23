"""include ptp_litebeam in the infra set of the RPC functions

Le nouveau device_type `ptp_litebeam` (migration b5c6d7e8f9a0) doit compter
comme INFRASTRUCTURE dans les fonctions RPC, sinon les liens P2P n'apparaissent
ni dans le compteur « équipements infra » d'un site, ni dans les pannes/outage,
ni dans le KPI dashboard (symptôme : « 0 équipement infra » sur un site qui n'a
que des PTP). On redéfinit les 3 fonctions concernées avec `_INFRA` élargi.
Reproduit les corps ACTUELS (fn_site_overview ← g4b5c6d7e8f9 ; fn_dashboard_summary
+ fn_site_outage_summary ← c0d1e2f3a4b5), seul le tuple `_INFRA` change.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-06-23 13:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)
_INFRA_NEW = "('rocket','uisp_switch','uisp_power','airfiber','ptp_litebeam')"
_INFRA_OLD = "('rocket','uisp_switch','uisp_power','airfiber')"


def _site_overview(infra: str) -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_site_overview()")
    op.execute(
        f"""
        CREATE FUNCTION fn_site_overview() RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        WITH power_metrics AS (
            SELECT DISTINCT ON (dm.device_id, dm.metric_name)
                   dm.device_id, dm.metric_name, dm.metric_value
              FROM device_metrics dm
             WHERE dm.device_id IN (SELECT id FROM devices WHERE device_type = 'uisp_power')
               AND (dm.metric_name LIKE 'battery_%_pct' OR dm.metric_name = 'ac_connected')
             ORDER BY dm.device_id, dm.metric_name, dm.collected_at DESC
        ),
        power_dev AS (
            SELECT d.id, d.name, d.status, d.site,
                   CASE
                     WHEN bool_or(pm.metric_name = 'ac_connected') THEN
                       CASE WHEN max(pm.metric_value)
                                 FILTER (WHERE pm.metric_name = 'ac_connected') >= 1
                            THEN 'mains' ELSE 'battery' END
                     ELSE NULL
                   END AS power_source,
                   COALESCE(
                     jsonb_agg(
                       jsonb_build_object(
                         'slug', substring(pm.metric_name from 'battery_(.+)_pct'),
                         'pct',  pm.metric_value)
                       ORDER BY CASE substring(pm.metric_name from 'battery_(.+)_pct')
                                  WHEN 'li_ion' THEN 0 WHEN 'lead_acid' THEN 1 ELSE 99 END
                     ) FILTER (WHERE pm.metric_name LIKE 'battery_%_pct'),
                     '[]'::jsonb
                   ) AS batteries
              FROM devices d
              LEFT JOIN power_metrics pm ON pm.device_id = d.id
             WHERE d.device_type = 'uisp_power'
             GROUP BY d.id, d.name, d.status, d.site
        ),
        power_by_site AS (
            SELECT site,
                   jsonb_agg(jsonb_build_object(
                       'id', id, 'name', name, 'status', status,
                       'power_source', power_source, 'batteries', batteries
                   ) ORDER BY name) AS power_devices
              FROM power_dev GROUP BY site
        ),
        site_agg AS (
            SELECT d.site AS name,
                   count(*) FILTER (WHERE d.device_type IN {infra}) AS infra,
                   count(*) FILTER (WHERE d.device_type = 'lr'
                                      AND d.status = 'up') AS clients_online,
                   count(*) FILTER (WHERE d.device_type = 'lr'
                                      AND l.client_blocked) AS clients_blocked,
                   count(*) FILTER (WHERE d.device_type IN {infra}
                                      AND d.status = 'down') AS pannes,
                   min(d.last_seen) FILTER (WHERE d.device_type IN {infra}
                                      AND d.status = 'down') AS down_since,
                   COALESCE(
                     jsonb_agg(jsonb_build_object(
                         'id', d.id, 'name', d.name, 'device_type', d.device_type,
                         'ip_address', d.ip_address,
                         'status', d.status, 'last_seen', d.last_seen
                     ) ORDER BY d.last_seen)
                     FILTER (WHERE d.device_type IN {infra} AND d.status = 'down'),
                     '[]'::jsonb
                   ) AS down_devices
              FROM devices d
              LEFT JOIN lrs l ON l.id = d.id
             GROUP BY d.site
        )
        SELECT COALESCE(jsonb_agg(
                   jsonb_build_object(
                       'name', s.name, 'infra', s.infra,
                       'clients_online', s.clients_online,
                       'clients_blocked', s.clients_blocked,
                       'pannes', s.pannes, 'down_since', s.down_since,
                       'down_devices', s.down_devices,
                       'power_devices', COALESCE(p.power_devices, '[]'::jsonb)
                   ) ORDER BY lower(s.name)
               ), '[]'::jsonb)
          FROM site_agg s
          LEFT JOIN power_by_site p ON p.site = s.name
        $$
        """
    )


def _dashboard_summary(infra: str) -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_dashboard_summary()")
    op.execute(
        f"""
        CREATE FUNCTION fn_dashboard_summary() RETURNS jsonb
        LANGUAGE sql STABLE AS $$
            SELECT jsonb_build_object(
                'total',          count(*),
                'up',             count(*) FILTER (WHERE status = 'up'),
                'down',           count(*) FILTER (WHERE status = 'down'),
                'sites',          count(DISTINCT site),
                'pannes',         count(*) FILTER (
                                      WHERE device_type IN {infra} AND status = 'down'),
                'clients',        count(*) FILTER (WHERE device_type = 'lr'),
                'open_incidents', (SELECT count(*) FROM incidents WHERE status = 'open')
            )
            FROM devices
        $$
        """
    )


def _site_outage_summary(infra: str) -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)"
    )
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
               AND d.device_type IN {infra}
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
                  FROM per_site WHERE downtime_seconds > 0
            ), '[]'::jsonb)
        )
        $$
        """
    )


def upgrade() -> None:
    _site_overview(_INFRA_NEW)
    _dashboard_summary(_INFRA_NEW)
    _site_outage_summary(_INFRA_NEW)


def downgrade() -> None:
    _site_overview(_INFRA_OLD)
    _dashboard_summary(_INFRA_OLD)
    _site_outage_summary(_INFRA_OLD)
