"""
Rattrape la colonne « Origine » des lignes de journal écrites avant la détection
du script appelant (commit f644fa0).

Pourquoi ce n'est pas une simple relecture du fichier
-----------------------------------------------------
Le journal n'enregistre PAS le `reason` envoyé par l'appelant — seulement le
message de résultat. L'origine d'une vieille ligne n'est donc pas déductible du
fichier seul. Elle l'est en revanche de la BASE : `lrs.client_blocked_reason`
conserve le motif du blocage en cours, et c'est lui qui porte la signature du
script (« Impaye - blocage auto - client ... » → Block_all.php).

Limite assumée : le motif est effacé au déblocage (`unblock_client`). Les lignes
des clients **débloqués depuis** restent donc en « payment » — l'information
n'existe plus nulle part, on ne l'invente pas. Idem pour un client bloqué par un
script puis re-bloqué par un autre : seul le dernier motif subsiste, donc on ne
réécrit que les lignes dont l'origine est encore certaine.

Ne touche QUE les lignes `BLOCK` dont l'origine vaut `payment` ; le fichier est
sauvegardé avant réécriture.

Usage (dans le conteneur backend) :
    dc exec backend python scripts/backfill_journal_source.py --dry-run
    dc exec backend python scripts/backfill_journal_source.py
"""

import argparse
import asyncio
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.api.endpoints.fai import _source_from_reason
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.device import Device, Lr

_DEFAULT_SOURCE = "payment"


async def _reason_by_mac() -> dict[str, str]:
    """MAC (minuscule) → motif du blocage EN COURS, pour les LR encore bloqués."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Device.mac_address, Lr.client_blocked_reason)
            .join(Lr, Lr.id == Device.id)
            .where(
                Lr.client_blocked.is_(True),
                Lr.client_blocked_reason.is_not(None),
                Device.mac_address.is_not(None),
            )
        )
        return {mac.lower(): reason for mac, reason in result.all()}


def _rewrite(line: str, reasons: dict[str, str]) -> tuple[str, str | None]:
    """Retourne (ligne, nouvelle_origine_ou_None).

    Le découpage reprend celui de `fai_audit._parse` (maxsplit=7, le message est
    le dernier champ et peut contenir des « | »), et on ne remplace QUE le champ
    source — le reste de la ligne est réécrit à l'identique.
    """
    parts = line.rstrip("\n").split(" | ", 7)
    if len(parts) < 8:
        return line, None
    action, mac, source = parts[1].strip(), parts[3].strip(), parts[6].strip()
    if action != "BLOCK" or source != f"source={_DEFAULT_SOURCE}":
        return line, None

    reason = reasons.get(mac.lower())
    if reason is None:
        return line, None  # client débloqué depuis → motif perdu, on n'invente pas
    resolved = _source_from_reason(reason)
    if resolved == _DEFAULT_SOURCE:
        return line, None  # motif connu mais sans signature → l'origine était juste

    parts[6] = f"source={resolved}"
    return " | ".join(parts) + "\n", resolved


async def run(dry_run: bool) -> None:
    path = get_settings().fai_log_path
    if not os.path.exists(path):
        print(f"Aucun journal à traiter ({path}).")
        return

    reasons = await _reason_by_mac()
    print(f"{len(reasons)} client(s) encore bloqué(s) avec un motif exploitable.")

    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    out: list[str] = []
    changed: dict[str, int] = {}
    for line in lines:
        new_line, resolved = _rewrite(line, reasons)
        out.append(new_line)
        if resolved:
            changed[resolved] = changed.get(resolved, 0) + 1

    total = sum(changed.values())
    if not total:
        print("Aucune ligne à corriger.")
        return

    print(f"\n{total} ligne(s) à ré-attribuer :")
    for source, count in sorted(changed.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>5}  → {source}")

    if dry_run:
        print("\nDRY-RUN — fichier inchangé.")
        return

    backup = f"{path}.bak"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    print(f"\nJournal réécrit. Sauvegarde : {backup}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rattrape l'origine des anciennes lignes du journal FAI."
    )
    parser.add_argument("--dry-run", action="store_true", help="Prévisualiser sans écrire.")
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))
