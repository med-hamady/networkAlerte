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

from sqlalchemy import delete, select  # noqa: E402

from app.core.alert_constants import PING_FAILURE_STATE_KEY  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.models.alert_state import AlertState  # noqa: E402
from app.models.device import Lr  # noqa: E402


def _aware(value: datetime.datetime | None) -> datetime.datetime | None:
    """Une colonne timestamptz peut remonter naïve selon le driver.

    Comparer naïf et aware lèverait un TypeError au milieu d'un nettoyage de
    masse, à mi-parcours des écritures.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value


def is_confirmed(
    uisp_status: str | None,
    uisp_last_seen: datetime.datetime | None,
    last_discovered_at: datetime.datetime | None,
    since: datetime.datetime,
    trust_hours: int,
) -> bool:
    """Une source confirme-t-elle ENCORE l'IP portée par cette ligne ?

    Trois façons de la confirmer :
      * le radio a redécouvert la station APRÈS l'écriture suspecte (`since`) :
        l'IP a été réécrite par la source qui voit le terrain ;
      * UISP voit la station **en ligne** : l'adresse est celle de maintenant ;
      * UISP l'a vue il y a moins de `trust_hours` : le bail DHCP n'a quasi
        sûrement pas bougé.

    Ce dernier point est une FENÊTRE, pas un booléen `active`. Une station en
    panne depuis 1 h et une station disparue depuis 3 semaines portent toutes
    deux `disconnected`, mais leur dernière IP connue n'a pas la même valeur —
    le cas fondateur (LR 598, « en outage depuis 1 h ») avait une adresse juste,
    vérifiée sur l'équipement. La jeter aurait été une perte, pas une prudence.
    """
    if (uisp_status or "").lower() == "active":
        return True
    last_discovered_at = _aware(last_discovered_at)
    if last_discovered_at is not None and last_discovered_at > since:
        return True
    uisp_last_seen = _aware(uisp_last_seen)
    if uisp_last_seen is None:
        return False
    return uisp_last_seen > datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        hours=trust_hours
    )


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

        kept, cleared = [], []
        for lr in rows:
            if is_confirmed(
                lr.uisp_status, lr.uisp_last_seen, lr.last_discovered_at,
                since, args.trust_hours,
            ):
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
