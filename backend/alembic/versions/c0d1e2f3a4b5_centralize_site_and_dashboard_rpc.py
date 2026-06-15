"""centralize site resolution + dashboard/sites/access/outage RPC functions

Pass 1 of "centraliser la logique côté DB". Four dashboard pages used to load the
whole fleet (`GET /devices?limit=1000`, 600+ rows) and re-do grouping / counting /
sorting / parent-site resolution in JavaScript — slow and fragile (the same
`siteOf()` logic was re-implemented 4×). This migration moves all of it into the
database:

  - `devices.site` — a denormalised site name maintained by triggers. An LR
    inherits the site of its parent Rocket (`lrs.rocket_id → rockets.id =
    devices.id`); every other device uses its own `location`; fallback
    'Sans site'. This is the single home of the old `siteOf()`.

  - 4 RPC functions returning ready-to-render `jsonb`:
      * fn_dashboard_summary()                              → KPI bar
      * fn_site_overview()                                  → /sites cards
      * fn_access_clients(search, filter)                  → /access table + stats
      * fn_site_outage_summary(start, end, merge_gap)      → "pannes par site"

Thin FastAPI endpoints just `SELECT fn_*()`; the frontend only renders.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-06-13 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Infra device types — count as a site outage when down. Keep in sync with the
# frontend INFRA_TYPES (unified to include airfiber, a P2P backhaul = infra).
_INFRA = "('rocket','uisp_switch','uisp_power','airfiber')"

# Availability alert_types — a device fully unreachable. MUST stay in sync with
# AVAILABILITY_ALERT_TYPES in app/core/alert_constants.py (the downtime journal
# uses the same set). LRs are intentionally excluded (client-side outage).
_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)


def upgrade() -> None:
    # ── 1. Column + index ────────────────────────────────────────────────────
    op.execute("ALTER TABLE devices ADD COLUMN site TEXT")
    op.execute("CREATE INDEX ix_devices_site ON devices (site)")

    # ── 2. Site resolution helper ────────────────────────────────────────────
    op.execute(
        """
        CREATE FUNCTION fn_resolve_site(p_device_id int) RETURNS text
        LANGUAGE sql STABLE AS $$
            SELECT COALESCE(
                NULLIF(TRIM(
                    CASE WHEN d.device_type = 'lr' THEN rd.location ELSE d.location END
                ), ''),
                'Sans site'
            )
            FROM devices d
            LEFT JOIN lrs l     ON l.id = d.id AND d.device_type = 'lr'
            LEFT JOIN devices rd ON rd.id = l.rocket_id
            WHERE d.id = p_device_id
        $$
        """
    )

    # ── 3. Triggers maintaining devices.site ─────────────────────────────────
    # Trigger A — a non-LR device's own location. Scoped to (location,
    # device_type) so the 30 s ping job (status/last_seen UPDATE) never fires it.
    op.execute(
        """
        CREATE FUNCTION trg_device_set_site() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.device_type <> 'lr' THEN
                NEW.site := COALESCE(NULLIF(TRIM(NEW.location), ''), 'Sans site');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER device_set_site
            BEFORE INSERT OR UPDATE OF location, device_type ON devices
            FOR EACH ROW EXECUTE FUNCTION trg_device_set_site()
        """
    )

    # Trigger B — a Rocket's location change cascades to its child LRs. The
    # cascade UPDATE touches only `site`, so it re-fires neither A nor B.
    op.execute(
        """
        CREATE FUNCTION trg_rocket_cascade_site() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.device_type = 'rocket' THEN
                UPDATE devices
                   SET site = COALESCE(NULLIF(TRIM(NEW.location), ''), 'Sans site')
                 WHERE id IN (SELECT id FROM lrs WHERE rocket_id = NEW.id);
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER rocket_cascade_site
            AFTER UPDATE OF location ON devices
            FOR EACH ROW EXECUTE FUNCTION trg_rocket_cascade_site()
        """
    )

    # Trigger C — an LR's (re)assignment recomputes its site from the new Rocket.
    op.execute(
        """
        CREATE FUNCTION trg_lr_set_site() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_site text;
        BEGIN
            SELECT COALESCE(NULLIF(TRIM(rd.location), ''), 'Sans site')
              INTO v_site
              FROM devices rd
             WHERE rd.id = NEW.rocket_id;
            IF v_site IS NULL THEN
                v_site := 'Sans site';
            END IF;
            UPDATE devices SET site = v_site WHERE id = NEW.id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER lr_set_site
            AFTER INSERT OR UPDATE OF rocket_id ON lrs
            FOR EACH ROW EXECUTE FUNCTION trg_lr_set_site()
        """
    )

    # ── 4. Backfill existing rows ────────────────────────────────────────────
    op.execute("UPDATE devices SET site = fn_resolve_site(id)")

    # ── 5. RPC functions ─────────────────────────────────────────────────────
    # 5a. Dashboard KPI bar.
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
                                      WHERE device_type IN {_INFRA} AND status = 'down'),
                'clients',        count(*) FILTER (WHERE device_type = 'lr'),
                'open_incidents', (SELECT count(*) FROM incidents WHERE status = 'open')
            )
            FROM devices
        $$
        """
    )

    # 5b. /sites cards (per-site counts, down_since, down + power device lists).
    op.execute(
        f"""
        CREATE FUNCTION fn_site_overview() RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        WITH power_metrics AS (
            -- device_id scoped FIRST so the planner uses ix_device_metrics_lookup
            -- (device_id, metric_name, collected_at) instead of seq-scanning the
            -- multi-million-row device_metrics table. Same pattern as
            -- network_capacity_service._fetch_latest_capacity_metrics.
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

    # 5c. /access table + stats. search/filter applied in SQL; items sorted
    # bridge-first, then blocked, then name.
    op.execute(
        """
        CREATE FUNCTION fn_access_clients(p_search text, p_filter text) RETURNS jsonb
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
    )

    # 5d. "Pannes par site" — merge availability incidents per device
    # (gaps-and-islands), clip to window, aggregate per site. Mirrors
    # network_uptime_service._merge_episodes (keep the merge gap consistent).
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


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)")
    op.execute("DROP FUNCTION IF EXISTS fn_access_clients(text, text)")
    op.execute("DROP FUNCTION IF EXISTS fn_site_overview()")
    op.execute("DROP FUNCTION IF EXISTS fn_dashboard_summary()")
    op.execute("DROP TRIGGER IF EXISTS lr_set_site ON lrs")
    op.execute("DROP FUNCTION IF EXISTS trg_lr_set_site()")
    op.execute("DROP TRIGGER IF EXISTS rocket_cascade_site ON devices")
    op.execute("DROP FUNCTION IF EXISTS trg_rocket_cascade_site()")
    op.execute("DROP TRIGGER IF EXISTS device_set_site ON devices")
    op.execute("DROP FUNCTION IF EXISTS trg_device_set_site()")
    op.execute("DROP FUNCTION IF EXISTS fn_resolve_site(int)")
    op.execute("DROP INDEX IF EXISTS ix_devices_site")
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS site")
