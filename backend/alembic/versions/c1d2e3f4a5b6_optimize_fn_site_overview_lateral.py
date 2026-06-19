"""optimize fn_site_overview: LATERAL per power-device (index scan, not seq scan)

fn_site_overview() (page /sites) was slow in prod: the battery/AC sub-query
filtered device_metrics by `device_id IN (uisp_power ids) AND (metric_name LIKE
'battery_%_pct' OR = 'ac_connected')`. The planner refused the
ix_device_metrics_lookup index for that shape and did a Parallel Seq Scan over
the WHOLE device_metrics table — which holds the bytes-counter history (tens of
millions of rows). That scan dominated the whole function (multi-second stall,
page stuck on "Chargement…").

Rewrite the power_metrics CTE as a LATERAL per uisp_power device with an
EQUALITY on device_id → forces a per-device index scan on
ix_device_metrics_lookup (device_id leading column). Measured on a 3.5M-row
device_metrics: 220 ms → 4.7 ms for the sub-query, and it stays ~constant as the
table grows (only ~30 power devices × a tiny index scan). CREATE OR REPLACE is an
instant in-place catalog update.

Chains AFTER g4b5c6d7e8f9 (which added `ip_address` to down_devices) — the two
were developed in parallel and both redefine fn_site_overview; this keeps a
single linear head and a final function that has BOTH the LATERAL power_metrics
and the `ip_address` field in down_devices.

Revision ID: c1d2e3f4a5b6
Revises: g4b5c6d7e8f9
Create Date: 2026-06-19 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "g4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Only the power_metrics CTE differs between the two versions; the rest of the
# function body is identical. We template it so the two stay in sync.
_BODY_TEMPLATE = """
CREATE OR REPLACE FUNCTION fn_site_overview() RETURNS jsonb
LANGUAGE sql STABLE AS $$
WITH power_metrics AS (
{power_metrics}
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
           count(*) FILTER (WHERE d.device_type IN ('rocket','uisp_switch','uisp_power','airfiber')) AS infra,
           count(*) FILTER (WHERE d.device_type = 'lr'
                              AND d.status = 'up') AS clients_online,
           count(*) FILTER (WHERE d.device_type = 'lr'
                              AND l.client_blocked) AS clients_blocked,
           count(*) FILTER (WHERE d.device_type IN ('rocket','uisp_switch','uisp_power','airfiber')
                              AND d.status = 'down') AS pannes,
           min(d.last_seen) FILTER (WHERE d.device_type IN ('rocket','uisp_switch','uisp_power','airfiber')
                              AND d.status = 'down') AS down_since,
           COALESCE(
             jsonb_agg(jsonb_build_object(
                 'id', d.id, 'name', d.name, 'device_type', d.device_type,
                 'ip_address', d.ip_address,
                 'status', d.status, 'last_seen', d.last_seen
             ) ORDER BY d.last_seen)
             FILTER (WHERE d.device_type IN ('rocket','uisp_switch','uisp_power','airfiber') AND d.status = 'down'),
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

# New: LATERAL per uisp_power device → index scan on ix_device_metrics_lookup.
_POWER_METRICS_LATERAL = """
    SELECT d.id AS device_id, pm.metric_name, pm.metric_value
      FROM devices d
      CROSS JOIN LATERAL (
          SELECT DISTINCT ON (dm.metric_name) dm.metric_name, dm.metric_value
            FROM device_metrics dm
           WHERE dm.device_id = d.id
             AND (dm.metric_name LIKE 'battery_%_pct' OR dm.metric_name = 'ac_connected')
           ORDER BY dm.metric_name, dm.collected_at DESC
      ) pm
     WHERE d.device_type = 'uisp_power'
"""

# Old (migration c0d1e2f3a4b5): device_id IN (...) → planner seq-scanned device_metrics.
_POWER_METRICS_IN = """
    SELECT DISTINCT ON (dm.device_id, dm.metric_name)
           dm.device_id, dm.metric_name, dm.metric_value
      FROM device_metrics dm
     WHERE dm.device_id IN (SELECT id FROM devices WHERE device_type = 'uisp_power')
       AND (dm.metric_name LIKE 'battery_%_pct' OR dm.metric_name = 'ac_connected')
     ORDER BY dm.device_id, dm.metric_name, dm.collected_at DESC
"""


def upgrade() -> None:
    op.execute(_BODY_TEMPLATE.format(power_metrics=_POWER_METRICS_LATERAL))


def downgrade() -> None:
    op.execute(_BODY_TEMPLATE.format(power_metrics=_POWER_METRICS_IN))
