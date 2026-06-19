"""add ip_address to fn_site_overview down_devices

The /sites pannes modal used to resolve down_devices to full Device objects by
joining their ids against the (paginated) GET /devices list. Once the parc grows
past the list limit, those down infra devices can fall outside the returned page
→ the modal renders empty ("0 équipement hors ligne") while the card still
correctly shows "N en panne" (the count comes from this SQL, which sees the whole
table). To make the modal authoritative-source-based, down_devices now carries
ip_address so the frontend can render it directly without the /devices join.

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-19
"""

from alembic import op

revision = "g4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None

_INFRA = "('rocket','uisp_switch','uisp_power','airfiber')"


# down_devices jsonb_build_object — only difference between up/down is the
# presence of the 'ip_address' key.
def _create(with_ip: bool) -> None:
    ip_line = "'ip_address', d.ip_address," if with_ip else ""
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
                   count(*) FILTER (WHERE d.device_type IN {_INFRA}) AS infra,
                   count(*) FILTER (WHERE d.device_type = 'lr'
                                      AND d.status = 'up') AS clients_online,
                   count(*) FILTER (WHERE d.device_type = 'lr'
                                      AND l.client_blocked) AS clients_blocked,
                   count(*) FILTER (WHERE d.device_type IN {_INFRA}
                                      AND d.status = 'down') AS pannes,
                   min(d.last_seen) FILTER (WHERE d.device_type IN {_INFRA}
                                      AND d.status = 'down') AS down_since,
                   COALESCE(
                     jsonb_agg(jsonb_build_object(
                         'id', d.id, 'name', d.name, 'device_type', d.device_type,
                         {ip_line}
                         'status', d.status, 'last_seen', d.last_seen
                     ) ORDER BY d.last_seen)
                     FILTER (WHERE d.device_type IN {_INFRA} AND d.status = 'down'),
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


def upgrade() -> None:
    _create(with_ip=True)


def downgrade() -> None:
    _create(with_ip=False)
