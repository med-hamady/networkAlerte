r"""
Efface les IP de LR qu'AUCUNE source ne confirme plus.

Contexte (2026-07-22) : un passage du sync UISP lancé sans le garde-fou de
fraîcheur a écrit 174 IP sur des LR à partir de l'instantané UISP. Pour une
station **déconnectée**, cette IP n'est qu'un dernier état connu que le DHCP a
pu réattribuer entre-temps — UISP a d'ailleurs rendu la même adresse pour
plusieurs abonnés différents.

Pourquoi ce n'est pas cosmétique : une ligne qui porte l'IP d'un AUTRE abonné
fait pinger le mauvais équipement (faux « en ligne ») et surtout ferait
appliquer un **blocage FAI au mauvais client** — les opérations SSH ciblent
l'IP de la fiche.

Règle appliquée — on GARDE l'IP seulement si une source la confirme ENCORE :
  * le radio a redécouvert la station depuis l'écriture suspecte
    (`last_discovered_at > --since`) : la découverte a réécrit l'IP elle-même,
    et elle, elle voit le terrain ;
  * UISP voit la station **active** en ce moment : son IP est celle de
    maintenant ;
  * UISP l'a vue il y a moins de `--trust-hours` (défaut 24 h) : le bail DHCP
    n'a quasi sûrement pas bougé. C'est une FENÊTRE, pas un booléen `active` —
    une station en panne depuis 1 h et une disparue depuis 3 semaines portent
    toutes deux `disconnected`, mais leur dernière IP connue n'a pas la même
    valeur.
Sinon l'IP est effacée et le statut repasse à `unknown` — c'est-à-dire l'état
honnête : nous ne pouvons plus mesurer cet abonné. Rien n'est perdu, la
découverte lui rend son IP dès qu'un AP le rapporte.

Le compteur d'échecs de ping est purgé avec l'IP, comme le fait
`discovery_service._release_ip_if_held` : sans ça la station rebasculerait
« down » au premier paquet perdu après son retour.

Deux façons de cibler :

  * **liste d'IP sur stdin** (issue des logs du passage fautif) + `--since` :
    périmètre exact, à privilégier tant que les logs existent ;
  * **rien sur stdin** : le script prend TOUTE ligne portant une IP et applique
    la même règle, avec `--radio-hours` comme fenêtre de confiance côté radio.
    Périmètre plus large — les logs Docker tournent vite, c'est le repli quand
    la trace du passage fautif a disparu.

Usage :

    dc logs --since 2h backend \\
      | grep "source UISP, radio muet" \
      | grep -oE "[0-9]{1,3}(\.[0-9]{1,3}){3} \(source" | awk '{print $1}' | sort -u \\
      | dc exec -T backend python scripts/clear_unverified_ips.py \\
            --since 2026-07-22T12:40:00Z

    # puis, une fois les chiffres vérifiés :
    ... | dc exec -T backend python scripts/clear_unverified_ips.py \\
            --since 2026-07-22T12:40:00Z --apply

Sans `--apply`, le script n'écrit RIEN (dry-run par défaut, volontairement).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.models.device import Lr  # noqa: E402
from app.services import ip_hygiene_service  # noqa: E402
from app.services.ip_hygiene_service import (  # noqa: E402
    _aware,
    is_confirmed,
    plan_cleanup,
)

# La logique vit dans `app/services/ip_hygiene_service.py` : le job planifié
# (toutes les 12 h) et ce script doivent appliquer EXACTEMENT la même règle,
# et un job ne peut pas importer depuis `scripts/`. Ici il ne reste que le
# pilotage en ligne de commande.

__all__ = ["is_confirmed", "plan_cleanup"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        help="Instant de l'écriture suspecte, ISO 8601 UTC (ex. 2026-07-22T12:40:00Z). "
             "Une découverte radio POSTÉRIEURE confirme l'IP. Par défaut : "
             "maintenant moins --radio-hours.",
    )
    parser.add_argument(
        "--radio-hours", type=int, default=24,
        help="Sans --since : le radio confirme l'IP s'il a vu la station depuis "
             "moins de N heures (défaut 24).",
    )
    parser.add_argument(
        "--trust-hours", type=int, default=24,
        help="Une IP annoncée par UISP est crédible si la station a été vue depuis "
             "moins de N heures (défaut 24). Miroir de UISP_IP_TRUST_HOURS.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Écrit réellement. Sans ce drapeau, le script ne fait qu'afficher.",
    )
    args = parser.parse_args()

    if args.since:
        since = datetime.datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=datetime.UTC)
    else:
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            hours=args.radio_hours
        )

    # stdin peut être un terminal (mode « tout le parc ») : ne pas bloquer dessus.
    ips = set()
    if not sys.stdin.isatty():
        ips = {line.strip() for line in sys.stdin if line.strip()}

    async with async_session_factory() as session:
        query = select(Lr).where(Lr.ip_address.is_not(None))
        if ips:
            query = query.where(Lr.ip_address.in_(ips))
            print(f"Périmètre : les {len(ips)} IP fournies")
        else:
            print("Périmètre : TOUTES les lignes portant une IP "
                  f"(radio confirmant depuis moins de {args.radio_hours} h)")
        rows = (await session.execute(query)).scalars().all()

        kept, cleared = plan_cleanup(rows, since, args.trust_hours)
        if args.apply:
            await ip_hygiene_service.run_cleanup(
                session, apply=True, since=since,
                trust_hours=args.trust_hours, ips=ips or None,
            )

        print(f"Lignes examinées      : {len(rows)}")
        print(f"  confirmées (gardées): {len(kept)}")
        print(f"  non confirmées      : {len(cleared)}")
        for lr in kept[:10]:
            if (lr.uisp_status or "").lower() == "active":
                why = "UISP active"
            elif lr.last_discovered_at and _aware(lr.last_discovered_at) > since:
                why = "radio récent"
            else:
                why = "UISP récent"
            print(f"    GARDE  {lr.ip_address:<15} {lr.name[:40]:<40} ({why})")
        for lr, old_ip in cleared[:10]:
            print(f"    EFFACE {old_ip or '?':<15} {lr.name[:40]}")
        if len(cleared) > 10:
            print(f"    … et {len(cleared) - 10} autres")

        if args.apply:
            await session.commit()
            print("\n>>> APPLIQUÉ. La découverte rendra son IP à chaque station qui revient.")
        else:
            print("\n>>> DRY-RUN — rien n'a été écrit. Relancer avec --apply pour appliquer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
