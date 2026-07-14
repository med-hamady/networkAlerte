"""
Retrouve les LR des clients que le blocage de masse n'a pas pu toucher.

Deux catégories sortent du dry-run de `block_clients.py` :

  - **pending-XXXX** : la facturation n'a AUCUNE MAC pour ce client (le placeholder
    contient son idclient, pas une MAC). Rien à chercher avec.
  - **NOT FOUND** : la MAC de la facturation est valide mais aucun LR ne la porte
    (équipement remplacé, jamais découvert, hors parc supervisé).

Dans les deux cas le seul identifiant commun aux deux systèmes est le **numéro de
téléphone** : la facturation le met dans `numero_crm` et en tête de `info`
(`49453918Abdellahi Camara`), et nos LR sont nommés de la même façon
(`36086261-Toutoumedlimam`). On cherche donc le LR dont le NOM contient ce numéro —
c'est quasi-exact, là où un rapprochement sur le nom seul ferait n'importe quoi
(« Mohamed » revient des dizaines de fois dans le parc).

Le rapprochement par nom n'est utilisé qu'en dernier recours, et il est signalé
comme tel : NE JAMAIS bloquer sur cette base sans validation humaine.

Ce script est en LECTURE SEULE — il ne bloque rien, il ne touche pas la base.

Usage (dans le conteneur backend) :
    dc exec backend python scripts/find_missing_macs.py scripts/clients_to_block_2026_07_14.csv
    dc exec backend python scripts/find_missing_macs.py <csv> --csv-out /app/logs/macs_retrouvees.csv
"""

import argparse
import asyncio
import csv
import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.device import Lr
from app.schemas.device import normalize_mac

# Les numéros mauritaniens ont 8 chiffres. On les cherche partout où la facturation
# a pu les mettre (numero_crm, ou collés au nom dans `info`).
_PHONE_RE = re.compile(r"\d{8}")
# Seuil de similarité du rapprochement par NOM (dernier recours). Volontairement
# haut : un faux positif ici couperait le mauvais client.
_NAME_MIN_RATIO = 0.82


def _phones(row: dict) -> list[str]:
    """Tous les numéros à 8 chiffres trouvés sur la ligne, sans doublon."""
    blob = f"{row.get('numero_crm') or ''} {row.get('info') or ''}"
    blob = re.sub(r"\s+", "", blob)  # "47 62 20 57" → "47622057"
    seen: list[str] = []
    for m in _PHONE_RE.findall(blob):
        if m not in seen:
            seen.append(m)
    return seen


def _clean_name(label: str) -> str:
    """Le nom sans les chiffres ni la ponctuation, en minuscules."""
    return re.sub(r"[^a-z]+", " ", label.lower()).strip()


async def _load_targets(path: str) -> list[dict]:
    """Lignes du CSV dont la MAC est absente (pending-*) ou introuvable en base."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    targets: list[dict] = []
    async with async_session_factory() as session:
        for row in rows:
            raw = (row.get("mac") or "").strip()
            try:
                mac = normalize_mac(raw)
            except ValueError:
                row["_why"] = "aucune MAC (pending)"
                targets.append(row)
                continue
            found = await session.execute(select(Lr).where(Lr.mac_address == mac))
            if found.scalar_one_or_none() is None:
                row["_why"] = "MAC inconnue en base"
                targets.append(row)
    return targets


async def _search(session, row: dict) -> tuple[Lr | None, str]:
    """Retrouve le LR de ce client → (LR, méthode). (None, "") si rien de sûr."""
    for phone in _phones(row):
        result = await session.execute(select(Lr).where(Lr.name.contains(phone)))
        hits = list(result.scalars().all())
        if len(hits) == 1:
            return hits[0], f"téléphone {phone}"
        if len(hits) > 1:  # plusieurs LR portent ce numéro — à trancher à la main
            return None, f"AMBIGU : {len(hits)} LR contiennent {phone}"

    # Dernier recours : similarité sur le nom seul. Peu fiable → toujours signalé.
    label = _clean_name(row.get("info") or "")
    if not label:
        return None, ""
    result = await session.execute(select(Lr))
    best, best_ratio = None, 0.0
    for lr in result.scalars().all():
        ratio = difflib.SequenceMatcher(None, label, _clean_name(lr.name or "")).ratio()
        if ratio > best_ratio:
            best, best_ratio = lr, ratio
    if best is not None and best_ratio >= _NAME_MIN_RATIO:
        return best, f"nom ~{best_ratio:.0%} (À VÉRIFIER)"
    return None, ""


async def run(path: str, csv_out: str | None) -> None:
    targets = await _load_targets(path)
    print(f"{len(targets)} client(s) sans LR identifié dans le batch.\n")

    found: list[tuple[dict, Lr, str]] = []
    unresolved: list[tuple[dict, str]] = []

    async with async_session_factory() as session:
        for row in targets:
            lr, how = await _search(session, row)
            if lr is None:
                unresolved.append((row, how))
            else:
                found.append((row, lr, how))

    print("=" * 100)
    print(f"RETROUVÉS — {len(found)} client(s)\n")
    for row, lr, how in found:
        flag = " [DÉJÀ BLOQUÉ]" if lr.client_blocked else ""
        print(f"  idclient={row.get('idclient'):<6} {(row.get('info') or '')[:38]:<38}")
        print(f"     → LR « {lr.name} »{flag}")
        print(f"       MAC {lr.mac_address}   IP {lr.ip_address or '—'}   site {lr.site or '—'}")
        print(f"       trouvé par : {how}   (la facturation avait : {row.get('mac')})")
        print()

    if unresolved:
        print("=" * 100)
        print(f"NON RETROUVÉS — {len(unresolved)} client(s) : rien de fiable en base\n")
        for row, why in unresolved:
            note = f"  [{why}]" if why else ""
            print(f"  idclient={row.get('idclient'):<6} {(row.get('info') or '')[:50]:<50}{note}")

    if csv_out and found:
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["idclient", "info", "mac", "lr_name", "lr_ip", "site", "methode"])
            for row, lr, how in found:
                w.writerow([
                    row.get("idclient"), row.get("info"), lr.mac_address,
                    lr.name, lr.ip_address or "", lr.site or "", how,
                ])
        print(f"\nCSV écrit : {csv_out}")
        print("→ à relire AVANT de bloquer, puis à renvoyer à la facturation pour "
              "qu'elle complète ses fiches.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Retrouve par téléphone les LR des clients sans MAC exploitable."
    )
    parser.add_argument("csv_path", help="Le même CSV que block_clients.py")
    parser.add_argument("--csv-out", default=None, help="Écrit les correspondances trouvées.")
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"Fichier introuvable : {args.csv_path}")
        sys.exit(1)

    asyncio.run(run(args.csv_path, args.csv_out))
