"""/access : distinguer « hors supervision » et le sortir des accès actifs

Un LR SANS IP est hors du sweep de ping (`_ping_sweep` filtre
`ip_address IS NOT NULL`), donc plus RIEN ne mesure son état : il reste en
`status='unknown'`. Jusqu'ici ces lignes étaient comptées dans « Accès actif »
(le seul critère était `NOT client_blocked`), ce qui gonflait le chiffre avec
des abonnés dont aucune source ne dit quoi que ce soit — 124 sur ~1000 en prod
le 2026-07-22, soit 12 % du parc.

Un LR est « hors supervision » quand les DEUX sources se taisent :
  - il n'a pas d'IP (nous ne pouvons pas le sonder), ET
  - le contrôleur UISP ne l'a pas vu depuis `p_out_of_supervision_days`.

Le délai est un PARAMÈTRE (défaut 7 j) et non une constante gravée comme l'était
`long_offline` : l'endpoint passe `OUT_OF_SUPERVISION_DAYS`, donc l'opérateur
l'ajuste dans le `.env` sans migration. Distinct de `long_offline` (UISP > 1
mois), qui reste inchangé : celui-là parle d'absence prolongée, celui-ci d'une
absence de MESURE.

Aucune ligne n'est supprimée : la découverte récupère un LR hors supervision
dès qu'un AP le rapporte avec une IP du plan de management.

Revision ID: cc3d4e5f6a7b
Revises: bb2c3d4e5f6a
Create Date: 2026-07-22
"""

from alembic import op

revision = "cc3d4e5f6a7b"
down_revision = "bb2c3d4e5f6a"
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
           -- Les deux sources se taisent : pas d'IP (donc pas de ping possible)
           -- ET UISP ne l'a pas vu récemment. `uisp_last_seen IS NULL` compte
           -- comme un silence : jamais vu = jamais mesuré.
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
        -- « Accès actif » = non bloqué ET encore mesurable. Un abonné dont
        -- aucune source ne parle n'est pas un accès actif.
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

# Signature précédente (2 arguments) : `CREATE OR REPLACE` ne peut pas ajouter
# un paramètre, et la laisser en place créerait une surcharge fantôme que les
# anciens appels continueraient d'utiliser silencieusement.
_DROP_OLD = "DROP FUNCTION IF EXISTS fn_access_clients(text, text)"


def upgrade() -> None:
    op.execute(_DROP_OLD)
    op.execute(_BODY)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_access_clients(text, text, int)")
    op.execute(
        """
        CREATE FUNCTION fn_access_clients(p_search text, p_filter text)
            RETURNS jsonb
        LANGUAGE sql STABLE AS $$
        WITH all_lrs AS (
            SELECT d.id, d.name, d.ip_address,
                   l.client_blocked, l.block_mode,
                   l.client_blocked_reason, l.client_blocked_at,
                   l.client_block_enforced_at,
                   l.uisp_status, l.uisp_last_seen, l.uisp_ap_name,
                   COALESCE(l.uisp_mode, 'unknown') AS effective_mode,
                   (l.uisp_status = 'active') AS reachable,
                   (l.uisp_last_seen < now() - interval '30 days') AS long_offline
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
                count(*) FILTER (WHERE long_offline) AS disconnected
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
                     WHEN 'disconnected'     THEN long_offline
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
    )
