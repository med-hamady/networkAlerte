"""
Exporte en JSON le nom, l'adresse IP et le statut de chaque LR abonné.

Lecture seule — le script ne touche à rien, il lit la seule table `lrs`.

Sortie : un TABLEAU d'objets à exactement TROIS clés — `name`, `ip_address`,
`status`. C'est un contrat : ne rien y ajouter (ni site, ni MAC, ni uptime, ni
horodatage). Tout ce qui informe sur l'export lui-même (nombre de lignes,
exclusions) part sur **stderr**, donc `> fichier.json` ne récupère que le
document.

⚠️ **`status` ne vaut que `ONLINE` (`up`) ou `OFFLINE` (`down`).** La base porte
une TROISIÈME valeur, `unknown` — un LR que plus rien ne mesure (IP libérée au
churn DHCP → hors du sweep de ping) — et ces lignes sont **EXCLUES**, jamais
repliées sur `OFFLINE` : ce sont des états non constatés, les rendre hors ligne
ferait remonter des coupures qui n'ont pas eu lieu. L'export ne porte donc que
des états mesurés, et il est **plus court que le parc** ; le compte des exclus
est annoncé sur stderr.

⚠️ **Le statut n'est pas mesuré à l'instant de l'export** : il est écrit par le
sweep de ping (`_ping_sweep`), et ne bascule à `down` qu'au seuil anti-flap
(`PING_DOWN_THRESHOLD` échecs consécutifs), jamais sur un paquet perdu.

⚠️ **Les LR « hors supervision » sont EXCLUS** (sans IP ET non vus par UISP
depuis `OUT_OF_SUPERVISION_DAYS`) : aucune source ne parle d'eux, leur statut
est figé sur sa dernière valeur. Le compte des exclus est annoncé sur stderr.
La règle est importée de `schemas/device`, jamais recopiée.

Usage (dans le conteneur backend) :
    dc exec -T backend python scripts/dump_lr_uptime.py > lr_uptime.json
    dc exec backend python scripts/dump_lr_uptime.py --out /app/lr_uptime.json
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.device import Lr

# La règle « hors supervision » est IMPORTÉE, jamais recopiée : elle est déjà
# écrite deux fois (ici en Python pour la fiche, et en SQL dans
# `fn_access_clients`, migration cc3d4e5f6a7b) et les deux doivent rester
# d'accord. Une troisième copie divergerait au premier ajustement du délai.
from app.schemas.device import is_out_of_supervision

# `devices.status` porte TROIS valeurs, pas deux. « unknown » n'est pas une
# panne : c'est un LR que plus rien ne mesure (IP libérée au churn DHCP → il
# sort du sweep de ping). Le rendre OFFLINE affirmerait une coupure que
# personne n'a constatée — exactement la lecture fausse que le badge ambre de
# /access a été créé pour éviter. Il sort donc sous son propre libellé.
_STATUS_LABELS = {"up": "ONLINE", "down": "OFFLINE"}
_STATUS_FALLBACK = "UNKNOWN"


async def run(out_path: str | None) -> None:
    async with async_session_factory() as session:
        lrs = (await session.execute(select(Lr).order_by(Lr.name))).scalars().all()

        rows = []
        excluded = 0
        unknown = 0
        for lr in lrs:
            # « Hors supervision » = sans IP ET invisible pour UISP depuis
            # `OUT_OF_SUPERVISION_DAYS` : les deux sources se taisent, donc son
            # statut n'est plus qu'un vestige. Exclu, et COMPTÉ : une exclusion
            # silencieuse ferait passer l'export pour exhaustif.
            if is_out_of_supervision(lr.ip_address, lr.uisp_last_seen):
                excluded += 1
                continue
            # Statut indéterminé → EXCLU, jamais rendu OFFLINE : ce LR n'est pas
            # tombé, il n'est plus mesuré (IP libérée au churn DHCP → hors du
            # sweep de ping). L'export ne porte donc que des états CONSTATÉS.
            label = _STATUS_LABELS.get(lr.status, _STATUS_FALLBACK)
            if label == _STATUS_FALLBACK:
                unknown += 1
                continue
            # TROIS clés, jamais une de plus : ce JSON est un contrat.
            rows.append({
                "name": lr.name,
                "ip_address": lr.ip_address,
                "status": label,
            })

    text = json.dumps(rows, ensure_ascii=False, indent=2)

    # Les comptes vont sur STDERR, jamais dans le JSON : ils restent lisibles à
    # l'exécution sans polluer un document qui ne doit porter que les 3 champs.
    online = sum(1 for r in rows if r["status"] == "ONLINE")
    summary = (
        f"{len(rows)} LR ({online} ONLINE, {len(rows) - online} OFFLINE) — "
        f"exclus : {unknown} au statut indéterminé, {excluded} hors supervision."
    )

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"{summary} Écrit dans {out_path}.", file=sys.stderr)
    else:
        print(text)
        print(summary, file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Exporte nom / IP / statut de chaque LR abonné en JSON.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Fichier de sortie (défaut : stdout, pour être redirigé).",
    )
    args = parser.parse_args()

    asyncio.run(run(args.out))
