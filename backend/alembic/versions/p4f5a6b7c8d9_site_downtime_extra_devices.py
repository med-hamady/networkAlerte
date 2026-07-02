"""site downtime: expose per-device downtime BEYOND the switch outage

Suite de o3e4f5a6b7c8. Le temps de panne d'un site = downtime du switch parent.
Mais un équipement peut rester down plus longtemps que le switch (le switch
revient up, un Rocket reste down). Ce surplus n'est PAS couvert par le chiffre
du site → on l'expose pour l'afficher à côté.

Physiquement : switch down ⟹ tous les équipements du site down (et enregistrés
comme tels), donc les intervalles de coupure du switch sont inclus dans ceux de
chaque équipement. Le surplus d'un équipement = `panne_équipement −
panne_switch` (clampé à 0) = le temps où l'équipement était seul en panne
pendant que le switch était up.

`fn_site_outage_summary.by_downtime` gagne une clé `extra_devices` par site :
liste des équipements NON-switch dont le surplus > 0, avec `extra_downtime_seconds`,
triés décroissant. `[]` si aucun. `by_pannes` et le reste : inchangés.

Revision ID: p4f5a6b7c8d9
Revises: o3e4f5a6b7c8
Create Date: 2026-07-02 15:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "p4f5a6b7c8d9"
down_revision: str | None = "o3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)
_INFRA = "('rocket','uisp_switch','uisp_power','airfiber','ptp_litebeam')"


def _create(with_extra_devices: bool) -> None:
    """(Re)create fn_site_outage_summary.

    When `with_extra_devices` is True, by_downtime sites carry an
    `extra_devices` array (new behaviour). When False, they don't (the
    o3e4f5a6b7c8 shape, for downgrade).
    """
    op.execute(
        "DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)"
    )

    if with_extra_devices:
        extra_ctes = """
        , extra_dev AS (
            -- Per non-switch device, downtime beyond the site's switch outage.
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
        )
        """
        per_site_downtime = """
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
        """
        by_downtime_obj = """
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices,
                           'extra_devices', extra_devices
                       ) ORDER BY downtime_seconds DESC)
                  FROM per_site_downtime WHERE downtime_seconds > 0
        """
    else:
        extra_ctes = ""
        per_site_downtime = """
        per_site_downtime AS (
            SELECT ps.site,
                   ps.pannes,
                   sw.downtime_seconds,
                   ps.devices
              FROM per_site ps
              JOIN switch_site sw ON sw.site = ps.site
        )
        """
        by_downtime_obj = """
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices
                       ) ORDER BY downtime_seconds DESC)
                  FROM per_site_downtime WHERE downtime_seconds > 0
        """

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
                   sum(total_downtime_seconds) AS downtime_seconds
              FROM per_device
             WHERE device_type = 'uisp_switch'
             GROUP BY site
        ){extra_ctes},
        {per_site_downtime}
        SELECT jsonb_build_object(
            'by_pannes', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'site', site, 'pannes', pannes,
                           'downtime_seconds', downtime_seconds, 'devices', devices
                       ) ORDER BY pannes DESC)
                  FROM per_site WHERE pannes > 0
            ), '[]'::jsonb),
            'by_downtime', COALESCE(({by_downtime_obj}
            ), '[]'::jsonb)
        )
        $$
        """
    )


def upgrade() -> None:
    _create(with_extra_devices=True)


def downgrade() -> None:
    _create(with_extra_devices=False)
