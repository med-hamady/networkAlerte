"""
Rollback d'un blocage de masse — rétablit les clients d'un CSV.

Reprend le MÊME CSV que `block_clients.py` : on ne lève que les blocages posés par
ce batch, jamais ceux qu'un opérateur a posés par ailleurs.

Deux chemins, selon ce qui a réellement été fait sur l'équipement :

  - `client_block_enforced_at IS NULL` → le SSH n'est **jamais passé** : le port du
    LR n'a jamais été coupé par nous. Il n'y a donc RIEN à rétablir sur l'équipement,
    il suffit d'effacer l'intention en base pour que le job de renforcement cesse de
    vouloir le couper. Pas de SSH → pas d'attente sur des équipements éteints.
  - sinon → le port a bien été fermé : on rétablit vraiment, via
    `client_block_service.unblock_client` (SSH). Si le LR est injoignable à cet
    instant, `unblock_pending` est posé et le job rejouera le rétablissement.

Usage (dans le conteneur backend) :
    dc exec backend python scripts/unblock_clients.py scripts/clients_to_block_2026_07_14.csv --dry-run
    dc exec backend python scripts/unblock_clients.py scripts/clients_to_block_2026_07_14.csv
"""

import argparse
import asyncio
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.schemas.device import normalize_mac
from app.services import client_block_service, fai_audit


def _load_macs(path: str) -> list[str]:
    """MAC normalisées et dédupliquées du CSV (les `pending-*` sont ignorées)."""
    macs: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("mac") or "").strip()
            if not raw:
                continue
            try:
                mac = normalize_mac(raw)
            except ValueError:
                continue
            if mac not in macs:
                macs.append(mac)
    return macs


async def _unblock_one(
    sem: asyncio.Semaphore, mac: str, dry_run: bool, results: dict[str, list]
) -> None:
    async with sem:
        async with async_session_factory() as session:
            lr = await client_block_service.find_lr_by_mac(session, mac)
            if lr is None:
                results["not_found"].append(mac)
                return
            if not lr.client_blocked:
                results["not_blocked"].append((mac, lr.name))
                return

            # Jamais appliqué sur le LR → rien à défaire sur l'équipement.
            if lr.client_block_enforced_at is None:
                if dry_run:
                    results["would_clear"].append((mac, lr.name))
                    return
                lr.client_blocked = False
                lr.client_blocked_at = None
                lr.client_blocked_reason = None
                lr.unblock_pending = False
                lr.block_unenforceable_reason = None
                await session.commit()
                fai_audit.log_action(
                    "UNBLOCK", ok=True, mac=lr.mac_address, name=lr.name,
                    mode=lr.block_mode, source="script",
                    message="Rollback : blocage jamais appliqué sur le LR, intention effacée.",
                )
                results["cleared"].append((mac, lr.name))
                return

            # Réellement coupé → rétablissement SSH.
            if dry_run:
                results["would_restore"].append((mac, lr.name))
                return
            try:
                ok, msg = await client_block_service.unblock_client(session, lr)
            except Exception as exc:  # une erreur ne doit pas arrêter le lot
                await session.rollback()
                results["pending"].append((mac, lr.name, f"exception: {exc}"))
                return
            fai_audit.log_action(
                "UNBLOCK", ok=ok, mac=lr.mac_address, name=lr.name,
                mode=lr.block_mode, source="script", message=msg,
            )
            (results["restored"] if ok else results["pending"]).append(
                (mac, lr.name) if ok else (mac, lr.name, msg)
            )


def _report(results: dict[str, list], dry_run: bool) -> None:
    print("\n" + "=" * 70)
    if dry_run:
        print(f"DRY-RUN — {len(results['would_restore'])} LR à rétablir en SSH "
              f"(réellement coupés)")
        print(f"DRY-RUN — {len(results['would_clear'])} LR à simplement dé-marquer "
              f"(jamais coupés)")
    else:
        print(f"Rétablis en SSH                      : {len(results['restored'])}")
        print(f"Dé-marqués (jamais coupés)           : {len(results['cleared'])}")
        pending = results["pending"]
        print(f"Intention levée, SSH à rejouer       : {len(pending)}")
        for item in pending:
            print(f"  [PENDING] {item[0]}  {item[1]} : {item[2]}")

    if results["not_blocked"]:
        print(f"\nDéjà actifs (rien à faire) : {len(results['not_blocked'])}")
    if results["not_found"]:
        print(f"MAC introuvable en base    : {len(results['not_found'])}")
    print("=" * 70)


async def run(path: str, dry_run: bool, concurrency: int) -> None:
    macs = _load_macs(path)
    print(f"{len(macs)} MAC dans le fichier. "
          f"{'DRY-RUN' if dry_run else 'ROLLBACK EN COURS'}.")

    results: dict[str, list] = {
        "restored": [], "cleared": [], "pending": [], "not_found": [],
        "not_blocked": [], "would_restore": [], "would_clear": [],
    }
    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(_unblock_one(sem, mac, dry_run, results) for mac in macs))
    _report(results, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rollback d'un blocage de masse (CSV).")
    parser.add_argument("csv_path", help="Le MÊME CSV que block_clients.py.")
    parser.add_argument("--dry-run", action="store_true", help="Prévisualiser sans écrire.")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"Fichier introuvable : {args.csv_path}")
        sys.exit(1)

    s = get_settings()
    pool_cap = s.db_pool_size + s.db_max_overflow
    concurrency = max(1, min(args.concurrency, pool_cap - 1))

    asyncio.run(run(args.csv_path, args.dry_run, concurrency))
