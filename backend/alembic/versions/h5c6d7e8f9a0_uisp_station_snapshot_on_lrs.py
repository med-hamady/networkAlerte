"""UISP station snapshot on lrs + /access shows down clients

Adds the `uisp_*` columns to `lrs` (controller's last-known mode/status for each
client station, imported by sync_uisp_stations) and rewrites fn_access_clients
so /access is sourced ENTIRELY from UISP (no live poll):
  - lists ALL LR clients, not only status='up' (a down Rocket no longer hides
    its clients),
  - bridge/router mode comes ONLY from uisp_mode (the live topology_mode poll is
    no longer read here — it broke whenever the Rocket/LR was down),
  - reachable/disconnected comes from uisp_status (the controller's view), not
    from our own ping,
  - exposes uisp_status / uisp_last_seen / uisp_ap_name and a `disconnected`
    stat + filter.

Revision ID: h5c6d7e8f9a0
Revises: c1d2e3f4a5b6
Create Date: 2026-06-22
"""

import sqlalchemy as sa

from alembic import op

revision = "h5c6d7e8f9a0"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


_ACCESS_NEW = """
CREATE OR REPLACE FUNCTION fn_access_clients(p_search text, p_filter text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
WITH all_lrs AS (
    SELECT d.id, d.name, d.ip_address,
           l.client_blocked, l.block_mode,
           l.client_blocked_reason, l.client_blocked_at,
           l.client_block_enforced_at,
           l.uisp_status, l.uisp_last_seen, l.uisp_ap_name,
           COALESCE(l.uisp_mode, 'unknown') AS effective_mode,
           (l.uisp_status = 'active') AS reachable
      FROM devices d JOIN lrs l ON l.id = d.id
     WHERE d.device_type = 'lr'
),
stats AS (
    SELECT
        count(*) AS total,
        count(*) FILTER (WHERE NOT client_blocked) AS active,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'full') AS blocked_full,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'whatsapp_only') AS blocked_whatsapp,
        count(*) FILTER (WHERE effective_mode = 'bridge') AS bridge,
        count(*) FILTER (WHERE NOT reachable) AS disconnected
      FROM all_lrs
),
filtered AS (
    SELECT * FROM all_lrs
     WHERE (p_search IS NULL OR p_search = ''
            OR name ILIKE '%' || p_search || '%'
            OR ip_address ILIKE '%' || p_search || '%')
       AND CASE COALESCE(p_filter, 'all')
             WHEN 'active'           THEN NOT client_blocked
             WHEN 'blocked_full'     THEN client_blocked AND block_mode = 'full'
             WHEN 'blocked_whatsapp' THEN client_blocked
                                          AND block_mode = 'whatsapp_only'
             WHEN 'bridge'           THEN effective_mode = 'bridge'
             WHEN 'disconnected'     THEN NOT reachable
             ELSE TRUE
           END
)
SELECT jsonb_build_object(
    'stats', (SELECT to_jsonb(stats) FROM stats),
    'items', COALESCE((
        SELECT jsonb_agg(to_jsonb(f) ORDER BY
                   CASE WHEN f.effective_mode = 'bridge' THEN 0 ELSE 1 END,
                   CASE WHEN f.client_blocked THEN 0 ELSE 1 END,
                   CASE WHEN f.reachable THEN 0 ELSE 1 END,
                   f.name)
          FROM filtered f
    ), '[]'::jsonb)
)
$$
"""

# Original definition (status='up' only, mode straight from topology_mode).
_ACCESS_OLD = """
CREATE OR REPLACE FUNCTION fn_access_clients(p_search text, p_filter text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
WITH up_lrs AS (
    SELECT d.id, d.name, d.ip_address, d.status,
           l.topology_mode, l.client_blocked, l.block_mode,
           l.client_blocked_reason, l.client_blocked_at,
           l.client_block_enforced_at
      FROM devices d JOIN lrs l ON l.id = d.id
     WHERE d.device_type = 'lr' AND d.status = 'up'
),
stats AS (
    SELECT
        (SELECT count(*) FROM devices WHERE device_type = 'lr') AS total,
        count(*) FILTER (WHERE NOT client_blocked) AS active,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'full') AS blocked_full,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'whatsapp_only') AS blocked_whatsapp,
        count(*) FILTER (WHERE topology_mode = 'bridge') AS bridge
      FROM up_lrs
),
filtered AS (
    SELECT * FROM up_lrs
     WHERE (p_search IS NULL OR p_search = ''
            OR name ILIKE '%' || p_search || '%'
            OR ip_address ILIKE '%' || p_search || '%')
       AND CASE COALESCE(p_filter, 'all')
             WHEN 'active'           THEN NOT client_blocked
             WHEN 'blocked_full'     THEN client_blocked AND block_mode = 'full'
             WHEN 'blocked_whatsapp' THEN client_blocked
                                          AND block_mode = 'whatsapp_only'
             WHEN 'bridge'           THEN topology_mode = 'bridge'
             ELSE TRUE
           END
)
SELECT jsonb_build_object(
    'stats', (SELECT to_jsonb(stats) FROM stats),
    'items', COALESCE((
        SELECT jsonb_agg(to_jsonb(f) ORDER BY
                   CASE WHEN f.topology_mode = 'bridge' THEN 0 ELSE 1 END,
                   CASE WHEN f.client_blocked THEN 0 ELSE 1 END,
                   f.name)
          FROM filtered f
    ), '[]'::jsonb)
)
$$
"""


def upgrade() -> None:
    op.add_column("lrs", sa.Column("uisp_mode", sa.String(length=10), nullable=True))
    op.add_column("lrs", sa.Column("uisp_status", sa.String(length=20), nullable=True))
    op.add_column(
        "lrs", sa.Column("uisp_last_seen", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("lrs", sa.Column("uisp_ap_name", sa.String(length=120), nullable=True))
    op.add_column(
        "lrs", sa.Column("uisp_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(_ACCESS_NEW)


def downgrade() -> None:
    op.execute(_ACCESS_OLD)
    op.drop_column("lrs", "uisp_synced_at")
    op.drop_column("lrs", "uisp_ap_name")
    op.drop_column("lrs", "uisp_last_seen")
    op.drop_column("lrs", "uisp_status")
    op.drop_column("lrs", "uisp_mode")
