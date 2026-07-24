"""/access : décomposer « hors supervision » par ancienneté (7 / 30 / 90 j)

La vue « hors supervision » regroupait tous les LR sans IP que UISP n'a pas vus
depuis le seuil (7 j). L'opérateur veut distinguer QUI est parti depuis longtemps
(≥ 90 j, quasi sûrement churné) de qui vient juste de disparaître (7-30 j, peut
revenir) — le même découpage que celui utilisé pour le blocage de masse sur le
routeur.

On ajoute donc, sans rien retirer :
  - `days_offline` par ligne : jours depuis `uisp_last_seen` (NULL = jamais vu) ;
  - stats `out_of_supervision_30d` / `_90d` : hors supervision ET down ≥ 30 / 90 j
    (les jamais-vus, `uisp_last_seen NULL`, ne comptent QUE dans la base 7 j —
    on ne peut pas les vieillir) ;
  - filtres `out_of_supervision_30d` / `out_of_supervision_90d`.

`out_of_supervision` (base, ≥ seuil param) reste inchangé.

Revision ID: v4i5j6k7l8m9
Revises: u3h4i5j6k7l8
"""

from alembic import op

revision = "v4i5j6k7l8m9"
down_revision = "u3h4i5j6k7l8"
branch_labels = None
depends_on = None


_BODY = """
CREATE OR REPLACE FUNCTION fn_access_clients(
    p_search text,
    p_filter text,
    p_out_of_supervision_days int DEFAULT 7
) RETURNS jsonb
LANGUAGE sql STABLE AS $$
WITH all_lrs AS (
    SELECT d.id, d.name, d.ip_address,
           l.client_blocked, l.block_mode,
           l.client_blocked_reason, l.client_blocked_at,
           l.client_block_enforced_at,
           l.uisp_status, l.uisp_last_seen, l.uisp_ap_name,
           COALESCE(l.uisp_mode, 'unknown') AS effective_mode,
           (l.uisp_status = 'active') AS reachable,
           (l.uisp_last_seen < now() - interval '30 days') AS long_offline,
           -- Jours depuis la dernière vue UISP (NULL = jamais vu). Sert à
           -- l'affichage « hors supervision depuis X j » et aux buckets.
           CASE WHEN l.uisp_last_seen IS NULL THEN NULL
                ELSE floor(extract(epoch FROM now() - l.uisp_last_seen) / 86400)::int
           END AS days_offline,
           (d.ip_address IS NULL
            AND (l.uisp_last_seen IS NULL
                 OR l.uisp_last_seen < now()
                    - make_interval(days => p_out_of_supervision_days)))
               AS out_of_supervision,
           -- Buckets d'ancienneté : hors supervision ET down depuis ≥ 30 / 90 j.
           -- uisp_last_seen NULL → la comparaison vaut NULL (donc faux) : les
           -- jamais-vus ne tombent que dans la base, jamais dans 30/90 j.
           (d.ip_address IS NULL
            AND l.uisp_last_seen < now() - interval '30 days') AS oos_30d,
           (d.ip_address IS NULL
            AND l.uisp_last_seen < now() - interval '90 days') AS oos_90d
      FROM devices d JOIN lrs l ON l.id = d.id
     WHERE d.device_type = 'lr'
),
stats AS (
    SELECT
        count(*) AS total,
        count(*) FILTER (WHERE NOT client_blocked
                           AND NOT out_of_supervision) AS active,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'full') AS blocked_full,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'whatsapp_only') AS blocked_whatsapp,
        count(*) FILTER (WHERE effective_mode = 'bridge') AS bridge,
        count(*) FILTER (WHERE long_offline) AS disconnected,
        count(*) FILTER (WHERE out_of_supervision) AS out_of_supervision,
        count(*) FILTER (WHERE oos_30d) AS out_of_supervision_30d,
        count(*) FILTER (WHERE oos_90d) AS out_of_supervision_90d
      FROM all_lrs
),
filtered AS (
    SELECT * FROM all_lrs
     WHERE (p_search IS NULL OR p_search = ''
            OR name ILIKE '%' || p_search || '%'
            OR ip_address ILIKE '%' || p_search || '%')
       AND CASE COALESCE(p_filter, 'all')
             WHEN 'active'                 THEN NOT client_blocked
                                                  AND NOT out_of_supervision
             WHEN 'blocked_full'           THEN client_blocked AND block_mode = 'full'
             WHEN 'blocked_whatsapp'       THEN client_blocked
                                                  AND block_mode = 'whatsapp_only'
             WHEN 'bridge'                 THEN effective_mode = 'bridge'
             WHEN 'disconnected'           THEN long_offline
             WHEN 'out_of_supervision'     THEN out_of_supervision
             WHEN 'out_of_supervision_30d' THEN oos_30d
             WHEN 'out_of_supervision_90d' THEN oos_90d
             ELSE TRUE
           END
)
SELECT jsonb_build_object(
    'stats', (SELECT to_jsonb(stats) FROM stats),
    'items', COALESCE((
        SELECT jsonb_agg(to_jsonb(f) ORDER BY
                   CASE WHEN f.effective_mode = 'bridge' THEN 0 ELSE 1 END,
                   CASE WHEN f.client_blocked THEN 0 ELSE 1 END,
                   -- Au sein d'un filtre d'ancienneté, les plus vieux d'abord.
                   f.days_offline DESC NULLS LAST,
                   CASE WHEN f.reachable THEN 0 ELSE 1 END,
                   f.name)
          FROM filtered f
    ), '[]'::jsonb)
)
$$
"""

# Version à 3 arguments sans les buckets (état avant cette migration).
_PREV_BODY = """
CREATE OR REPLACE FUNCTION fn_access_clients(
    p_search text,
    p_filter text,
    p_out_of_supervision_days int DEFAULT 7
) RETURNS jsonb
LANGUAGE sql STABLE AS $$
WITH all_lrs AS (
    SELECT d.id, d.name, d.ip_address,
           l.client_blocked, l.block_mode,
           l.client_blocked_reason, l.client_blocked_at,
           l.client_block_enforced_at,
           l.uisp_status, l.uisp_last_seen, l.uisp_ap_name,
           COALESCE(l.uisp_mode, 'unknown') AS effective_mode,
           (l.uisp_status = 'active') AS reachable,
           (l.uisp_last_seen < now() - interval '30 days') AS long_offline,
           (d.ip_address IS NULL
            AND (l.uisp_last_seen IS NULL
                 OR l.uisp_last_seen < now()
                    - make_interval(days => p_out_of_supervision_days)))
               AS out_of_supervision
      FROM devices d JOIN lrs l ON l.id = d.id
     WHERE d.device_type = 'lr'
),
stats AS (
    SELECT
        count(*) AS total,
        count(*) FILTER (WHERE NOT client_blocked
                           AND NOT out_of_supervision) AS active,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'full') AS blocked_full,
        count(*) FILTER (WHERE client_blocked
                           AND block_mode = 'whatsapp_only') AS blocked_whatsapp,
        count(*) FILTER (WHERE effective_mode = 'bridge') AS bridge,
        count(*) FILTER (WHERE long_offline) AS disconnected,
        count(*) FILTER (WHERE out_of_supervision) AS out_of_supervision
      FROM all_lrs
),
filtered AS (
    SELECT * FROM all_lrs
     WHERE (p_search IS NULL OR p_search = ''
            OR name ILIKE '%' || p_search || '%'
            OR ip_address ILIKE '%' || p_search || '%')
       AND CASE COALESCE(p_filter, 'all')
             WHEN 'active'             THEN NOT client_blocked
                                              AND NOT out_of_supervision
             WHEN 'blocked_full'       THEN client_blocked AND block_mode = 'full'
             WHEN 'blocked_whatsapp'   THEN client_blocked
                                              AND block_mode = 'whatsapp_only'
             WHEN 'bridge'             THEN effective_mode = 'bridge'
             WHEN 'disconnected'       THEN long_offline
             WHEN 'out_of_supervision' THEN out_of_supervision
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


def upgrade() -> None:
    op.execute(_BODY)


def downgrade() -> None:
    op.execute(_PREV_BODY)
