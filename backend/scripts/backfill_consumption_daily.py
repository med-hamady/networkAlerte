r"""
Remplit `client_consumption_daily` à partir de l'historique de `device_metrics`.

Contexte (2026-09-24) : la consommation d'un client ne se lit pas, elle se
CALCULE par différences successives sur les compteurs d'octets cumulés relevés
toutes les minutes. Répondre à « combien en mars » obligeait donc à conserver
tous les relevés de mars — d'où une table sans aucune rétention, 58,4 M lignes
et 5,9 Go, qui grossissait sans fin.

Une journée écoulée ne change plus jamais. Ce script fait la soustraction UNE
fois par journée passée et enregistre le total : ~1 ligne par client, par
compteur et par jour. Le job de nuit `client_consumption_daily_rollup_job`
prend ensuite le relais pour la veille.

⚠️ À LANCER AVANT toute rétention sur `device_metrics`. Purger les relevés
bruts sans avoir rempli ce résumé perdrait l'historique de consommation
définitivement.

⚠️ Idempotent : rejouer une journée REMPLACE sa ligne (upsert), jamais de
doublon. On peut donc l'interrompre et le relancer sans précaution.

⚠️ Coût : chaque journée relit ses propres relevés (~1 à 2 min de disque par
jour d'historique sur la prod de 2026-09). Le script traite les journées de la
plus RÉCENTE à la plus ancienne : si on l'interrompt, ce qui est déjà couvert
est ce qui sert le plus. Un commit par journée — une interruption ne perd que
la journée en cours.

⚠️ Ne traite JAMAIS aujourd'hui : la journée n'est pas finie, son total serait
partiel et figé. La page la calcule en direct.

Usage :

    # ce que le script ferait, sans rien écrire
    dc exec -T backend python scripts/backfill_consumption_daily.py

    # remplir réellement tout l'historique disponible
    dc exec -T backend python scripts/backfill_consumption_daily.py --apply

    # une fenêtre précise, ou reprendre après interruption
    dc exec -T backend python scripts/backfill_consumption_daily.py --apply \
        --from 2026-07-01 --to 2026-08-31

    # recalculer des journées déjà présentes (correction)
    dc exec -T backend python scripts/backfill_consumption_daily.py --apply --force

Sans `--apply`, le script n'écrit RIEN (dry-run par défaut, volontairement).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.models.client_consumption_daily import ClientConsumptionDaily  # noqa: E402
from app.services import consumption_service  # noqa: E402


def _parse_day(value: str) -> datetime.date:
    return datetime.datetime.strptime(value, "%Y-%m-%d").date()


async def _oldest_sample_day() -> datetime.date | None:
    """Jour du plus ancien relevé de compteur encore présent."""
    async with async_session_factory() as session:
        oldest = await session.scalar(
            text(
                "SELECT min(collected_at) FROM device_metrics "
                "WHERE metric_name = ANY(CAST(:metric_names AS text[]))"
            ),
            {"metric_names": list(consumption_service._COUNTER_METRICS)},
        )
    return oldest.date() if oldest is not None else None


async def _already_done() -> set[datetime.date]:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(ClientConsumptionDaily.day).group_by(ClientConsumptionDaily.day)
        )
    return {r[0] for r in rows}


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remplit le résumé quotidien de consommation."
    )
    parser.add_argument("--from", dest="from_day", type=_parse_day,
                        help="Premier jour (AAAA-MM-JJ). Défaut : plus ancien relevé.")
    parser.add_argument("--to", dest="to_day", type=_parse_day,
                        help="Dernier jour inclus. Défaut : hier.")
    parser.add_argument("--apply", action="store_true",
                        help="Écrit réellement (sinon dry-run).")
    parser.add_argument("--force", action="store_true",
                        help="Recalcule aussi les journées déjà présentes.")
    args = parser.parse_args()

    today = datetime.datetime.now(datetime.UTC).date()
    last_day = args.to_day or (today - datetime.timedelta(days=1))
    if last_day >= today:
        # Garde-fou : une journée en cours donnerait un total partiel que plus
        # rien ne viendrait corriger.
        print(f"⚠️  --to ramené à hier ({today - datetime.timedelta(days=1)}) : "
              "la journée en cours n'est pas totalisable.")
        last_day = today - datetime.timedelta(days=1)

    first_day = args.from_day
    if first_day is None:
        first_day = await _oldest_sample_day()
        if first_day is None:
            print("Aucun relevé de compteur dans device_metrics — rien à faire.")
            return 0

    if first_day > last_day:
        print(f"Rien à traiter : {first_day} > {last_day}.")
        return 0

    done = set() if args.force else await _already_done()

    # De la plus récente à la plus ancienne : une interruption laisse alors
    # couvert ce qui est le plus consulté.
    days = []
    day = last_day
    while day >= first_day:
        if day not in done:
            days.append(day)
        day -= datetime.timedelta(days=1)

    print(f"Période      : {first_day} → {last_day}")
    print(f"Déjà présent : {len(done)} journée(s)")
    print(f"À traiter    : {len(days)} journée(s)")
    if not args.apply:
        print("\nDRY-RUN — rien n'est écrit. Relancer avec --apply.")
        return 0
    if not days:
        return 0

    total_rows = 0
    started = datetime.datetime.now(datetime.UTC)
    for index, day in enumerate(days, start=1):
        day_started = datetime.datetime.now(datetime.UTC)
        # Une session par journée : le commit est par journée, donc une erreur
        # n'annule jamais les journées déjà écrites.
        async with async_session_factory() as session:
            rows = await consumption_service.rollup_day(session, day)
            await session.commit()
        total_rows += rows
        elapsed = (datetime.datetime.now(datetime.UTC) - day_started).total_seconds()
        print(f"[{index}/{len(days)}] {day} : {rows:>6} ligne(s) en {elapsed:5.1f} s",
              flush=True)

    total_elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
    print(f"\nTerminé : {len(days)} journée(s), {total_rows} ligne(s) "
          f"en {total_elapsed / 60:.1f} min")

    async with async_session_factory() as session:
        span = await session.execute(
            select(func.min(ClientConsumptionDaily.day),
                   func.max(ClientConsumptionDaily.day),
                   func.count())
            .select_from(ClientConsumptionDaily)
        )
        low, high, count = span.one()
    print(f"Résumé en base : {count} ligne(s), du {low} au {high}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
