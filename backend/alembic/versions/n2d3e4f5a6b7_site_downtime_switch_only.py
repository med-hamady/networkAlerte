"""site downtime = parent switch downtime only (fn_site_outage_summary)

Le temps de panne d'un site était la SOMME des downtimes de tous ses équipements
infra. C'est faux : un site avec 3 Rockets + 1 switch + 1 AirFiber tous down 4 h
affichait ~20 h au lieu de 4 h (le même créneau compté 5×).

Nouveau modèle : le **switch est l'équipement parent** du site — s'il est down,
tout le site est down. Donc le temps de panne d'un site = le downtime de son
switch (`uisp_switch`), pas plus, pas moins.

Ce qui change dans `fn_site_outage_summary` :
  - `by_pannes`   (graphe « Nombre de pannes par site ») : INCHANGÉ — compte
    toujours les épisodes de coupure de TOUT l'infra (utile pour l'instabilité).
  - `by_downtime` (graphe « Temps de panne par site »)   : `downtime_seconds`
    d'un site = downtime de son/ses switch(es) uniquement ; la liste `devices`
    du drill-down ne montre que le(s) switch(es) (pour que la durée affichée
    corresponde). `pannes` reste le compte all-infra (colonne du rapport). Un
    site sans switch (ou dont le switch n'a jamais été down) → 0 → masqué.

Reproduit le corps ACTUEL (← c6d7e8f9a0b1), seul l'agrégat par site du downtime
diffère. `_INFRA` / `_AVAIL` restent alignés sur alert_constants.

Revision ID: n2d3e4f5a6b7
Revises: m1c2d3e4f5a6
Create Date: 2026-07-02 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "n2d3e4f5a6b7"
down_revision: str | None = "m1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AVAIL = (
    "('rocket_down','switch_down','device_unreachable',"
    "'uisp_power_unreachable','airmax_down')"
)
_INFRA = "('rocket','uisp_switch','uisp_power','airfiber','ptp_litebeam')"


def _create(switch_only_downtime: bool) -> None:
    """(Re)create fn_site_outage_summary.

    When `switch_only_downtime` is True the per-site downtime is taken from the
    site's switch(es) only (new behaviour). When False it sums every infra
    device (legacy behaviour, for downgrade).
    """
    op.execute(
        "DROP FUNCTION IF EXISTS fn_site_outage_summary(timestamptz, timestamptz, int)"
    )

    if switch_only_downtime:
        # by_downtime: switch-only downtime, joined to the all-infra panne count
        # so the report table keeps a meaningful "Nombre de pannes" column. The
        # drill-down devices list holds the switch(es) only.
        by_downtime_ctes = f"""
        , switch_site AS (
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
            SELECT sw.site,
                   ps.pannes,
                   sw.downtime_seconds,
                   sw.devices
              FROM switch_site sw
              JOIN per_site ps ON ps.site = sw.site
        )
        """
        by_downtime_src = "per_site_downtime"
    else:
        by_downtime_ctes = ""
        by_downtime_src = "per_site"

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
        ){by_downtime_ctes}
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
                  FROM {by_downtime_src} WHERE downtime_seconds > 0
            ), '[]'::jsonb)
        )
        $$
        """
    )


def upgrade() -> None:
    _create(switch_only_downtime=True)


def downgrade() -> None:
    _create(switch_only_downtime=False)
