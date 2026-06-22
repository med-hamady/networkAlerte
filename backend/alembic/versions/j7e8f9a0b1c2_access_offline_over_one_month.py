"""/access: « hors ligne » = LR non vu par UISP depuis > 1 mois (au lieu de 1 sem.)

Même logique que i6d7e8f9a0b1, mais le seuil `long_offline` passe de 7 jours à
30 jours (l'opérateur ne veut suivre que les clients absents depuis plus d'un
mois). Seule la valeur de l'intervalle change dans fn_access_clients.

Revision ID: j7e8f9a0b1c2
Revises: i6d7e8f9a0b1
Create Date: 2026-06-22
"""

from alembic import op

revision = "j7e8f9a0b1c2"
down_revision = "i6d7e8f9a0b1"
branch_labels = None
depends_on = None


def _create(offline_interval: str) -> None:
    """(Re)create fn_access_clients with the given « hors ligne » age window."""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION fn_access_clients(p_search text, p_filter text)
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
                   (l.uisp_last_seen < now() - interval '{offline_interval}') AS long_offline
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


def upgrade() -> None:
    _create("30 days")


def downgrade() -> None:
    _create("7 days")
