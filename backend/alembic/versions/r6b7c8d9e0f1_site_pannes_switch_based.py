"""site « nombre de pannes » = switch parent (comme le temps de panne)

Le graphe « Nombre de pannes par site » (by_pannes) sommait les épisodes de
coupure de TOUS les équipements infra → chiffre gonflé, même problème que le
temps de panne avant n2d3e4f5a6b7. On aligne sa logique sur « Temps de panne » :
le nombre de pannes d'un site = le nombre d'épisodes de coupure de son SWITCH
parent (s'il est down, le site est down).

fn_site_outage_summary, désormais symétrique pour les deux sorties :
  - `pannes`          = nombre d'épisodes du/des switch(es) du site (both by_pannes
    et by_downtime).
  - `downtime_seconds`= downtime du/des switch(es) (inchangé).
  - `devices`         = TOUS les équipements infra (drill-down, inchangé).
  - `extra_devices`   = équipements non-switch au-delà du switch. by_downtime →
    surplus de TEMPS (`extra_downtime_seconds`) ; by_pannes → surplus de PANNES
    (`extra_episodes`). Chaque entrée porte les deux champs.
Site sans switch (ou switch jamais down) → 0 → masqué.

Revision ID: r6b7c8d9e0f1
Revises: q5a6b7c8d9e0
Create Date: 2026-07-02 18:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "r6b7c8d9e0f1"
down_revision: str | None = "q5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)
_INFRA = "('rocket','uisp_switch','uisp_power','airfiber','ptp_litebeam')"

# Common CTE prelude (base → per_device → per_site → switch_site), shared by both
# the new (switch-based pannes) and old (all-infra pannes) function bodies.
_PRELUDE = f"""
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
"""


def _create_new() -> None:
    """Switch-based pannes + downtime, symmetric extra_devices."""
    op.execute(
        "DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)"
    )
    op.execute(
        f"""
        CREATE FUNCTION fn_site_outage_summary(
            p_start timestamptz, p_end timestamptz, p_merge_gap int
        ) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        {_PRELUDE},
        switch_site AS (
            SELECT site,
                   sum(total_downtime_seconds) AS downtime_seconds,
                   sum(episodes_count)         AS pannes
              FROM per_device
             WHERE device_type = 'uisp_switch'
             GROUP BY site
        ),
        extra_dev AS (
            -- Per non-switch device: downtime AND episodes beyond the switch.
            SELECT pd.site, pd.device_id, pd.device_name, pd.device_type,
                   GREATEST(0, pd.total_downtime_seconds - sw.downtime_seconds)
                       AS extra_downtime_seconds,
                   GREATEST(0, pd.episodes_count - sw.pannes) AS extra_episodes
              FROM per_device pd
              JOIN switch_site sw ON sw.site = pd.site
             WHERE pd.device_type <> 'uisp_switch'
        ),
        extra_downtime_site AS (
            SELECT site,
                   jsonb_agg(jsonb_build_object(
                       'device_id', device_id, 'device_name', device_name,
                       'device_type', device_type,
                       'extra_downtime_seconds', extra_downtime_seconds,
                       'extra_episodes', extra_episodes
                   ) ORDER BY extra_downtime_seconds DESC) AS extra_devices
              FROM extra_dev
             WHERE extra_downtime_seconds > 0
             GROUP BY site
        ),
        extra_pannes_site AS (
            SELECT site,
                   jsonb_agg(jsonb_build_object(
                       'device_id', device_id, 'device_name', device_name,
                       'device_type', device_type,
                       'extra_downtime_seconds', extra_downtime_seconds,
                       'extra_episodes', extra_episodes
                   ) ORDER BY extra_episodes DESC) AS extra_devices
              FROM extra_dev
             WHERE extra_episodes > 0
             GROUP BY site
        ),
        per_site_final AS (
            SELECT ps.site,
                   sw.pannes,
                   sw.downtime_seconds,
                   ps.devices,
                   COALESCE(ed.extra_devices, '[]'::jsonb) AS extra_downtime_devices,
                   COALESCE(ep.extra_devices, '[]'::jsonb) AS extra_pannes_devices
              FROM per_site ps
              JOIN switch_site sw ON sw.site = ps.site
              LEFT JOIN extra_downtime_site ed ON ed.site = ps.site
              LEFT JOIN extra_pannes_site  ep ON ep.site = ps.site
        )
        SELECT jsonb_build_object(
            'by_pannes', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices,
                           'extra_devices', extra_pannes_devices
                       ) ORDER BY pannes DESC)
                  FROM per_site_final WHERE pannes > 0
            ), '[]'::jsonb),
            'by_downtime', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices,
                           'extra_devices', extra_downtime_devices
                       ) ORDER BY downtime_seconds DESC)
                  FROM per_site_final WHERE downtime_seconds > 0
            ), '[]'::jsonb)
        )
        $$
        """
    )


def _create_old() -> None:
    """Restore p4f5a6b7c8d9 shape: by_pannes = all-infra, by_downtime = switch."""
    op.execute(
        "DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)"
    )
    op.execute(
        f"""
        CREATE FUNCTION fn_site_outage_summary(
            p_start timestamptz, p_end timestamptz, p_merge_gap int
        ) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        {_PRELUDE},
        switch_site AS (
            SELECT site,
                   sum(total_downtime_seconds) AS downtime_seconds
              FROM per_device
             WHERE device_type = 'uisp_switch'
             GROUP BY site
        ),
        extra_dev AS (
            SELECT pd.site, pd.device_id, pd.device_name, pd.device_type,
                   GREATEST(0, pd.total_downtime_seconds - sw.downtime_seconds)
                       AS extra_downtime_seconds
              FROM per_device pd
              JOIN switch_site sw ON sw.site = pd.site
             WHERE pd.device_type <> 'uisp_switch'
        ),
        extra_by_site AS (
            SELECT site,
                   jsonb_agg(jsonb_build_object(
                       'device_id', device_id, 'device_name', device_name,
                       'device_type', device_type,
                       'extra_downtime_seconds', extra_downtime_seconds
                   ) ORDER BY extra_downtime_seconds DESC) AS extra_devices
              FROM extra_dev
             WHERE extra_downtime_seconds > 0
             GROUP BY site
        ),
        per_site_downtime AS (
            SELECT ps.site,
                   ps.pannes,
                   sw.downtime_seconds,
                   ps.devices,
                   COALESCE(eb.extra_devices, '[]'::jsonb) AS extra_devices
              FROM per_site ps
              JOIN switch_site sw ON sw.site = ps.site
              LEFT JOIN extra_by_site eb ON eb.site = ps.site
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
                           'downtime_seconds', downtime_seconds, 'devices', devices,
                           'extra_devices', extra_devices
                       ) ORDER BY downtime_seconds DESC)
                  FROM per_site_downtime WHERE downtime_seconds > 0
            ), '[]'::jsonb)
        )
        $$
        """
    )


def upgrade() -> None:
    _create_new()


def downgrade() -> None:
    _create_old()
