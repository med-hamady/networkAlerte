"""
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
    et elle, elle voit le terrain ; ou
  * UISP voit la station **active** en ce moment (`uisp_status = 'active'`) :
    son IP est alors actuelle, pas un souvenir.
Sinon l'IP est effacée et le statut repasse à `unknown` — c'est-à-dire l'état
honnête : nous ne pouvons plus mesurer cet abonné. Rien n'est perdu, la
découverte lui rend son IP dès qu'un AP le rapporte.

Le compteur d'échecs de ping est purgé avec l'IP, comme le fait
`discovery_service._release_ip_if_held` : sans ça la station rebasculerait
« down » au premier paquet perdu après son retour.

Usage — la liste d'IP vient des logs du passage fautif, sur stdin :

    dc logs --since 2h backend \\
      | grep -oE "IP reprise [^ ]+ → [0-9.]+" | awk '{print $NF}' | sort -u \\
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

from sqlalchemy import delete, select  # noqa: E402

from app.core.alert_constants import PING_FAILURE_STATE_KEY  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.models.alert_state import AlertState  # noqa: E402
from app.models.device import Lr  # noqa: E402


def is_confirmed(
    uisp_status: str | None,
    last_discovered_at: datetime.datetime | None,
    since: datetime.datetime,
) -> bool:
    """Une source confirme-t-elle ENCORE l'IP portée par cette ligne ?

    `since` = l'instant de l'écriture suspecte. Une découverte radio POSTÉRIEURE
    signifie que l'IP a été réécrite par la source qui voit le terrain, donc
    qu'elle est bonne quoi qu'ait fait le passage fautif.
    """
    if (uisp_status or "").lower() == "active":
        return True
    if last_discovered_at is None:
        return False
    if last_discovered_at.tzinfo is None:
        last_discovered_at = last_discovered_at.replace(tzinfo=datetime.UTC)
    return last_discovered_at > since


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since", required=True,
        help="Instant de l'écriture suspecte, ISO 8601 UTC (ex. 2026-07-22T12:40:00Z)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Écrit réellement. Sans ce drapeau, le script ne fait qu'afficher.",
    )
    args = parser.parse_args()

    since = datetime.datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    if since.tzinfo is None:
        since = since.replace(tzinfo=datetime.UTC)

    ips = {line.strip() for line in sys.stdin if line.strip()}
    if not ips:
        print("Aucune IP reçue sur stdin — rien à faire.")
        return 1

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(Lr).where(Lr.ip_address.in_(ips)))
        ).scalars().all()

        kept, cleared = [], []
        for lr in rows:
            if is_confirmed(lr.uisp_status, lr.last_discovered_at, since):
                kept.append(lr)
                continue
            cleared.append(lr)
            if args.apply:
                lr.ip_address = None
                lr.status = "unknown"
                await session.execute(
                    delete(AlertState).where(
                        AlertState.device_id == lr.id,
                        AlertState.alert_type == PING_FAILURE_STATE_KEY,
                    )
                )

        print(f"IP reçues            : {len(ips)}")
        print(f"Lignes correspondantes: {len(rows)}")
        print(f"  confirmées (gardées): {len(kept)}")
        print(f"  non confirmées      : {len(cleared)}")
        for lr in kept[:10]:
            why = "UISP active" if (lr.uisp_status or "").lower() == "active" else "radio récent"
            print(f"    GARDE  {lr.ip_address:<15} {lr.name[:40]:<40} ({why})")
        for lr in cleared[:10]:
            print(f"    EFFACE {lr.ip_address:<15} {lr.name[:40]}")
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
