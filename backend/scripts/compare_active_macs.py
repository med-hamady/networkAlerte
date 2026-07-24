"""
Compare nos clients ACTIFS à la liste d'un autre système.

« Actif » = exactement la tuile « Accès actif » de /access : un LR **non bloqué**
ET **pas hors supervision** (mêmes critères que `fn_access_clients`). C'est le
953 affiché.

Sortie principale : les MAC actives chez NOUS mais ABSENTES de la liste de l'autre
système — notre surplus. Affiche aussi l'inverse (chez eux, pas actifs chez nous)
à titre indicatif.

Les MAC sont normalisées des deux côtés (minuscule, deux-points) pour que des
notations différentes ne créent pas de faux écarts.

Usage (dans le conteneur backend) :
    # 1. déposer la liste de l'autre système (une MAC par ligne, guillemets/virgules
    #    tolérés) dans un fichier, ex. scripts/other_active.txt
    dc exec backend python scripts/compare_active_macs.py scripts/other_active.txt
    dc exec backend python scripts/compare_active_macs.py scripts/other_active.txt --csv-out /app/logs/surplus.csv
"""

import argparse
import asyncio
import contextlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.db.session import async_session_factory
from app.schemas.device import normalize_mac

_MAC_RE = re.compile(r"[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}|[0-9a-fA-F]{12}")


def _load_other(path: str) -> set[str]:
    """MAC de l'autre système → set normalisé. Tolère guillemets, virgules, en-tête."""
    macs: set[str] = set()
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            for token in _MAC_RE.findall(line):
                with contextlib.suppress(ValueError):
                    macs.add(normalize_mac(token))
    return macs


async def _our_active_macs() -> dict[str, str]:
    """MAC normalisée → nom du LR, pour nos clients ACTIFS (= tuile Accès actif).

    Actif = non bloqué ET pas hors supervision. Hors supervision = pas d'IP ET
    (jamais vu par UISP OU vu il y a plus de OUT_OF_SUPERVISION_DAYS).
    """
    async with async_session_factory() as session:
        rows = await session.execute(text("""
            SELECT d.mac_address, d.name
              FROM devices d JOIN lrs l ON l.id = d.id
             WHERE d.device_type = 'lr'
               AND d.mac_address IS NOT NULL
               AND NOT l.client_blocked
               AND NOT (
                   d.ip_address IS NULL
                   AND (l.uisp_last_seen IS NULL
                        OR l.uisp_last_seen < now()
                           - make_interval(days => :days))
               )
        """), {"days": _oos_days()})
        out: dict[str, str] = {}
        for mac, name in rows.all():
            with contextlib.suppress(ValueError):
                out[normalize_mac(mac)] = name
        return out


def _oos_days() -> int:
    from app.core.config import get_settings
    return get_settings().out_of_supervision_days


async def run(path: str, csv_out: str | None) -> None:
    other = _load_other(path)
    ours = await _our_active_macs()

    surplus = sorted(set(ours) - other)            # actifs chez nous, absents chez eux
    missing = sorted(other - set(ours))            # chez eux, pas actifs chez nous

    print(f"Nos actifs        : {len(ours)}")
    print(f"Liste autre système : {len(other)}")
    print(f"Communs           : {len(set(ours) & other)}")
    print("=" * 70)
    print(f"ACTIFS CHEZ NOUS, ABSENTS DE L'AUTRE SYSTÈME : {len(surplus)}\n")
    for mac in surplus:
        print(f"  {mac}  {ours[mac]}")

    print("\n" + "=" * 70)
    print(f"(Indicatif) Dans l'autre liste mais PAS actifs chez nous : {len(missing)}")
    for mac in missing:
        print(f"  {mac}")

    if csv_out:
        import csv
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["mac", "name"])
            for mac in surplus:
                w.writerow([mac, ours[mac]])
        print(f"\nSurplus écrit dans {csv_out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="MAC actives chez nous absentes d'une liste externe."
    )
    parser.add_argument("other_path", help="Fichier des MAC de l'autre système.")
    parser.add_argument("--csv-out", default=None, help="Écrit le surplus en CSV.")
    args = parser.parse_args()

    if not os.path.exists(args.other_path):
        print(f"Fichier introuvable : {args.other_path}")
        sys.exit(1)

    asyncio.run(run(args.other_path, args.csv_out))
