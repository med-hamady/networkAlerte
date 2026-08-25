"""retire la categorie de blocage « google » des filtres deja poses

Decision operateur du 2026-08-25 : la categorie « google » disparait du
catalogue. Couper google.com emportait gstatic / googleapis /
googleusercontent, donc reCAPTCHA, les cartes integrees et les polices — une
part enorme du web pour l'abonne, tres au-dela de ce que l'operateur croyait
cocher en la selectionnant.

Cette migration ne fait que du MENAGE DE DONNEES : sans elle, le runtime
laisserait la cle « google » dormir dans `lrs.blocked_categories` alors que
`_normalize_categories` la jette a chaque lecture. Le comportement serait le
meme, mais la colonne mentirait — et la page de filtrage afficherait un etat
que plus aucun code ne comprend.

⚠️ CE QUI CHANGE POUR LES ABONNES, ET CE N'EST PAS SYMETRIQUE :

  - en « denylist » (l'immense majorite : c'est le defaut, et le seul mode
    qu'un appel de l'API tierce produit), la cle listait ce qui est BLOQUE.
    La retirer REND Google accessible — exactement l'intention.

  - en « allowlist », la meme cle listait au contraire ce qui est JOIGNABLE.
    La retirer COUPE Google chez cet abonne, l'inverse de l'intention, et il
    n'existe plus aucun moyen de le re-autoriser puisque la cle a disparu du
    catalogue. Pire, si « google » y etait la SEULE entree, l'ensemble devient
    vide — or un ensemble vide EFFACE le filtre (cf. `set_content_block`),
    c'est-a-dire rouvre tout l'internet a cet abonne.

Ces lignes-la ne peuvent pas etre corrigees ici sans deviner une politique
commerciale : la migration les nettoie comme les autres (pour ne pas laisser
diverger le stocke et l'effectif) mais les NOMME dans le journal, afin qu'un
humain reprenne leur cas depuis le dashboard. La requete de controle a passer
AVANT le deploiement est dans le message de deploiement.

Revision ID: b1c2d3e4f5a6
Revises: e5b6c7d8f9a0
Create Date: 2026-08-25 10:00:00.000000

"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "e5b6c7d8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    conn = op.get_bind()

    # Signaler les lignes en « allowlist » AVANT de les toucher : ce sont les
    # seules dont le sens s'inverse, et une fois la cle retiree plus rien ne
    # permet de les retrouver.
    rows = conn.execute(
        sa.text(
            """
            SELECT d.id, d.name, l.content_block_mode,
                   jsonb_array_length(l.blocked_categories::jsonb) AS n
            FROM lrs l JOIN devices d ON d.id = l.id
            WHERE jsonb_exists(l.blocked_categories::jsonb, 'google')
              AND l.content_block_mode = 'allowlist'
            """,
        ),
    ).fetchall()
    for row in rows:
        logger.warning(
            "RETRAIT DE « google » — LR '%s' (id=%s) est en ALLOWLIST : Google y "
            "etait AUTORISE et devient injoignable%s. A reprendre a la main "
            "depuis /content-block.",
            row.name, row.id,
            " — c'etait sa SEULE entree, son filtre est donc entierement efface "
            "et cet abonne recupere tout l'internet" if row.n == 1 else "",
        )

    # Retrait de la cle, quel que soit le mode : le stocke doit refleter ce que
    # le runtime applique reellement, sinon la page affiche un etat fantome.
    # `- 'google'` sur un tableau jsonb retire l'element ; le cast garde le type
    # JSON d'origine de la colonne. `jsonb_exists` plutot que l'operateur `?`,
    # que certains pilotes lisent comme un marqueur de parametre.
    conn.execute(
        sa.text(
            """
            UPDATE lrs
            SET blocked_categories = ((blocked_categories::jsonb) - 'google')::json
            WHERE jsonb_exists(blocked_categories::jsonb, 'google')
            """,
        ),
    )


def downgrade() -> None:
    # Irreversible par nature : on ne sait pas quelles lignes portaient la cle,
    # et la reintroduire au hasard couperait Google chez des abonnes qui ne
    # l'ont jamais eu bloque. Le retour arriere se fait en re-declarant la
    # categorie dans le catalogue puis en la cochant la ou elle est voulue.
    pass
