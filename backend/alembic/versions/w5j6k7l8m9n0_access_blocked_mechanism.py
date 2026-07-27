"""/access : décomposer « Bloqués » par mécanisme (SSH sur le LR vs routeur)

Depuis le repli routeur, un client bloqué l'est soit sur son équipement (SSH a
réussi, `client_block_enforced_at` renseigné), soit — quand le LR ne répond pas —
sur le routeur de cœur (`router_blocked`). L'opérateur veut voir les deux
séparément sous « Bloqués ».

On ajoute à `fn_access_clients`, sans rien retirer :
  - `router_blocked` par ligne (pour le badge de mécanisme) ;
  - stats `blocked` (total), `blocked_ssh`, `blocked_router`, `blocked_pending` ;
  - filtres `blocked` / `blocked_ssh` / `blocked_router` / `blocked_pending`.

Mécanisme, mutuellement exclusif :
  - SSH      : `client_block_enforced_at IS NOT NULL` (coupé sur le LR).
  - routeur  : `router_blocked` ET pas encore coupé sur le LR.
  - en attente : ni l'un ni l'autre (le job rattrapera).

Revision ID: w5j6k7l8m9n0
Revises: v4i5j6k7l8m9
"""

from alembic import op

revision = "w5j6k7l8m9n0"
down_revision = "v4i5j6k7l8m9"
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
           l.client_block_enforced_at, l.router_blocked,
           l.uisp_status, l.uisp_last_seen, l.uisp_ap_name,
           COALESCE(l.uisp_mode, 'unknown') AS effective_mode,
           (l.uisp_status = 'active') AS reachable,
           (l.uisp_last_seen < now() - interval '30 days') AS long_offline,
           CASE WHEN l.uisp_last_seen IS NULL THEN NULL
                ELSE floor(extract(epoch FROM now() - l.uisp_last_seen) / 86400)::int
           END AS days_offline,
           (d.ip_address IS NULL
            AND (l.uisp_last_seen IS NULL
                 OR l.uisp_last_seen < now()
                    - make_interval(days => p_out_of_supervision_days)))
               AS out_of_supervision,
           (d.ip_address IS NULL
            AND l.uisp_last_seen < now() - interval '30 days') AS oos_30d,
           (d.ip_address IS NULL
            AND l.uisp_last_seen < now() - interval '90 days') AS oos_90d,
           -- Mécanisme de blocage, mutuellement exclusif.
           (l.client_blocked AND l.client_block_enforced_at IS NOT NULL) AS blk_ssh,
           (l.client_blocked AND l.router_blocked
            AND l.client_block_enforced_at IS NULL) AS blk_router,
           (l.client_blocked AND l.client_block_enforced_at IS NULL
            AND NOT l.router_blocked) AS blk_pending
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
        count(*) FILTER (WHERE oos_90d) AS out_of_supervision_90d,
        count(*) FILTER (WHERE client_blocked) AS blocked,
        count(*) FILTER (WHERE blk_ssh) AS blocked_ssh,
        count(*) FILTER (WHERE blk_router) AS blocked_router,
        count(*) FILTER (WHERE blk_pending) AS blocked_pending
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
             WHEN 'blocked'                THEN client_blocked
             WHEN 'blocked_ssh'            THEN blk_ssh
             WHEN 'blocked_router'         THEN blk_router
             WHEN 'blocked_pending'        THEN blk_pending
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
                   f.days_offline DESC NULLS LAST,
                   CASE WHEN f.reachable THEN 0 ELSE 1 END,
                   f.name)
          FROM filtered f
    ), '[]'::jsonb)
)
$$
"""

# Corps de la révision précédente (buckets d'ancienneté, sans les buckets de blocage).
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
           CASE WHEN l.uisp_last_seen IS NULL THEN NULL
                ELSE floor(extract(epoch FROM now() - l.uisp_last_seen) / 86400)::int
           END AS days_offline,
           (d.ip_address IS NULL
            AND (l.uisp_last_seen IS NULL
                 OR l.uisp_last_seen < now()
                    - make_interval(days => p_out_of_supervision_days)))
               AS out_of_supervision,
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
                   f.days_offline DESC NULLS LAST,
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
