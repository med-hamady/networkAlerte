"""
Blocage de masse sur le ROUTEUR des clients « hors supervision ».

Un client est « hors supervision » quand AUCUNE source ne parle de lui : il n'a
pas d'IP (donc hors du sweep de ping, injoignable en SSH) ET le contrôleur UISP
ne l'a pas vu depuis `OUT_OF_SUPERVISION_DAYS` jours. La règle est la même que
`schemas/device.is_out_of_supervision` et `fn_access_clients` — répliquée ici en
SQL pour ne sélectionner que ces LR.

Comme ils n'ont pas d'IP, on ne peut PAS les couper sur leur équipement (pas de
chemin SSH). Le seul levier est le **routeur de cœur**, qui coupe par MAC sans
rien demander à l'équipement du client. Ce script pose donc directement la règle
drop sur le routeur pour chaque MAC concernée.

État écrit en base, pour que le blocage soit **suivi et réversible** comme
n'importe quel autre :

  - `client_blocked=True` + `client_blocked_reason` = « Hors supervision … »
  - `router_blocked=True` + `router_blocked_at`
  - `client_block_enforced_at` reste NULL

Avec ça, `desired_router_block(lr)` vaut True (client bloqué, coupure LR non
confirmée) : le job de renforcement **maintient** la règle et ne la retire pas.
Si le client revient un jour (récupère une IP), le job basculera la coupure sur
son LR et retirera la règle routeur — convergence propre. Un `unblock` (API ou
dashboard) retire la règle et lève l'état, comme pour tout autre blocage.

⚠️ « Hors supervision » ≠ « impayé » : ce sont des clients qu'on a perdus de vue,
pas forcément des mauvais payeurs. Décision opérateur.

Usage (dans le conteneur backend) :
    dc exec backend python scripts/block_out_of_supervision.py --dry-run
    dc exec backend python scripts/block_out_of_supervision.py
    dc exec backend python scripts/block_out_of_supervision.py --limit 20   # 1er lot de test
"""

import argparse
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.device import Device, Lr
from app.services import client_block_service, fai_audit, mikrotik_service

DEFAULT_REASON = "Hors supervision — coupé sur le routeur"


async def _load_targets(limit: int | None) -> list[Lr]:
    """LR hors supervision (sans IP, UISP muet depuis le seuil) avec une MAC.

    Reprend `is_out_of_supervision` : ip NULL ET (uisp_last_seen NULL OU antérieur
    au seuil). Restreint aux LR portant une MAC — sans MAC, rien à bloquer.
    """
    horizon = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=get_settings().out_of_supervision_days
    )
    async with async_session_factory() as session:
        stmt = (
            select(Lr)
            .join(Device, Device.id == Lr.id)
            .where(
                Device.ip_address.is_(None),
                Device.mac_address.is_not(None),
                or_(Lr.uisp_last_seen.is_(None), Lr.uisp_last_seen < horizon),
            )
            .order_by(Device.name)
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _block_one(lr_id: int, reason: str, results: dict[str, list]) -> None:
    """Bloque un LR sur le routeur (session propre, sûr sous gather)."""
    async with async_session_factory() as session:
        lr = await session.get(Lr, lr_id)
        if lr is None:
            return
        if lr.router_blocked:
            results["already"].append((lr.mac_address, lr.name))
            return

        ok, msg = await mikrotik_service.block_by_mac(
            lr.mac_address, mikrotik_service.build_comment(f"hors-supervision {lr.name}"),
        )
        if not ok:
            results["failed"].append((lr.mac_address, lr.name, msg))
            fai_audit.log_action(
                "ROUTER_BLOCK", ok=False, mac=lr.mac_address, name=lr.name,
                mode=lr.block_mode, source="script", message=msg,
            )
            return

        # Suivi + réversibilité : l'état rend desired_router_block(lr) vrai, donc
        # le job maintient la règle au lieu de la retirer au cycle suivant.
        if not lr.client_blocked:
            lr.client_blocked = True
            lr.client_blocked_at = client_block_service._now()
        lr.client_blocked_reason = reason
        lr.router_blocked = True
        lr.router_blocked_at = client_block_service._now()
        await session.commit()

        fai_audit.log_action(
            "ROUTER_BLOCK", ok=True, mac=lr.mac_address, name=lr.name,
            mode=lr.block_mode, source="script", message=msg,
        )
        results["blocked"].append((lr.mac_address, lr.name))


def _report(results: dict[str, list], dry_run: bool, total: int) -> None:
    print("\n" + "=" * 70)
    if dry_run:
        print(f"DRY-RUN — {total} client(s) hors supervision seraient bloqués sur le routeur.")
        return
    print(f"Bloqués sur le routeur : {len(results['blocked'])}")
    print(f"Déjà bloqués (ignorés) : {len(results['already'])}")
    failed = results["failed"]
    print(f"Échecs routeur         : {len(failed)}")
    for mac, name, msg in failed:
        print(f"  [ÉCHEC] {mac}  {name} : {msg}")
    print("=" * 70)


async def run(dry_run: bool, limit: int | None, reason: str, concurrency: int) -> None:
    if not dry_run and not mikrotik_service.is_enabled():
        print("MikroTik désactivé (MIKROTIK_ENABLED / mot de passe) — rien à faire.")
        return

    targets = await _load_targets(limit)
    print(f"{len(targets)} LR hors supervision avec une MAC. "
          f"{'DRY-RUN' if dry_run else 'BLOCAGE ROUTEUR'}.")

    if dry_run:
        for lr in targets:
            print(f"  [MATCH] {lr.mac_address}  {lr.name}  (site {lr.site or '—'})")
        _report({}, dry_run=True, total=len(targets))
        return

    results: dict[str, list] = {"blocked": [], "already": [], "failed": []}
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(lr_id: int) -> None:
        async with sem:
            await _block_one(lr_id, reason, results)

    await asyncio.gather(*(_guarded(lr.id) for lr in targets))
    _report(results, dry_run=False, total=len(targets))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bloque sur le routeur les clients hors supervision."
    )
    parser.add_argument("--dry-run", action="store_true", help="Prévisualiser sans écrire.")
    parser.add_argument("--limit", type=int, default=None, help="Ne traiter que les N premiers.")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="Motif enregistré.")
    parser.add_argument("--concurrency", type=int, default=5, help="Blocages routeur en parallèle.")
    args = parser.parse_args()

    # Le routeur a sa propre borne (mikrotik_service), on reste sous le pool DB.
    s = get_settings()
    concurrency = max(1, min(args.concurrency, s.db_pool_size + s.db_max_overflow - 1))

    asyncio.run(run(args.dry_run, args.limit, args.reason, concurrency))
