"""
Blocage de masse sur le ROUTEUR des clients down depuis longtemps.

Cible les abonnés que UISP a provisionnés mais qui sont **hors ligne depuis
longtemps** ET dont on a perdu l'IP (donc hors du sweep de ping, injoignables en
SSH). Politique : un client mort depuis longtemps ne doit pas pouvoir se
reconnecter et avoir internet gratuitement — la règle drop sur le routeur est
inoffensive tant qu'il est down (aucun trafic à couper) et ne mord qu'à sa
reconnexion, moment où on veut qu'il paie d'abord.

⚠️ Le seuil compte. « hors supervision » (`is_out_of_supervision`) vaut 7 jours —
trop court, et il attrape aussi les `uisp_last_seen` NULL (jamais vus par UISP,
qui ne sont pas « down depuis longtemps » mais « jamais mesurés »). Ce script
prend donc :
  - `--min-days-down N` (défaut 30) : couper seulement au-delà de N jours down ;
  - `--include-never-seen` : inclure aussi les `uisp_last_seen` NULL (exclus par
    défaut — un provisionnement récent jamais monté ne doit pas être coupé).

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
from app.models.device import Lr
from app.services import client_block_service, fai_audit, mikrotik_service

DEFAULT_REASON = "Hors supervision — coupé sur le routeur"


async def _load_targets(
    limit: int | None, min_days_down: int, include_never_seen: bool
) -> list[Lr]:
    """LR down depuis ≥ `min_days_down` jours, sans IP, avec une MAC.

    Down = `uisp_last_seen` antérieur au seuil. `include_never_seen` ajoute les
    `uisp_last_seen` NULL (jamais vus par UISP) — exclus par défaut car « jamais
    vu » n'est pas « down depuis longtemps ». On exige `uisp_synced_at` non nul :
    on ne cible que des abonnés que UISP connaît, pas les fantômes radio.
    """
    horizon = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=min_days_down)
    down_clause = Lr.uisp_last_seen < horizon
    if include_never_seen:
        down_clause = or_(Lr.uisp_last_seen.is_(None), down_clause)
    async with async_session_factory() as session:
        # Lr hérite de Device (joined-table) → select(Lr) joint déjà `devices` ;
        # on filtre sur les colonnes héritées sans re-joindre (sinon alias dupliqué).
        stmt = (
            select(Lr)
            .where(
                Lr.ip_address.is_(None),
                Lr.mac_address.is_not(None),
                Lr.uisp_synced_at.is_not(None),
                down_clause,
            )
            .order_by(Lr.name)
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
        print(f"DRY-RUN — {total} client(s) down depuis longtemps seraient bloqués sur le routeur.")
        return
    print(f"Bloqués sur le routeur : {len(results['blocked'])}")
    print(f"Déjà bloqués (ignorés) : {len(results['already'])}")
    failed = results["failed"]
    print(f"Échecs routeur         : {len(failed)}")
    for mac, name, msg in failed:
        print(f"  [ÉCHEC] {mac}  {name} : {msg}")
    print("=" * 70)


async def run(
    dry_run: bool, limit: int | None, reason: str, concurrency: int,
    min_days_down: int, include_never_seen: bool,
) -> None:
    if not dry_run and not mikrotik_service.is_enabled():
        print("MikroTik désactivé (MIKROTIK_ENABLED / mot de passe) — rien à faire.")
        return

    targets = await _load_targets(limit, min_days_down, include_never_seen)
    never = " (+ jamais vus)" if include_never_seen else ""
    print(f"{len(targets)} LR down depuis ≥ {min_days_down} j{never}, sans IP, connus de UISP. "
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
        description="Bloque sur le routeur les clients down depuis longtemps."
    )
    parser.add_argument("--dry-run", action="store_true", help="Prévisualiser sans écrire.")
    parser.add_argument("--limit", type=int, default=None, help="Ne traiter que les N premiers.")
    parser.add_argument("--min-days-down", type=int, default=30,
                        help="Ne cibler que les LR down depuis au moins N jours (défaut 30).")
    parser.add_argument("--include-never-seen", action="store_true",
                        help="Inclure aussi les LR jamais vus par UISP (uisp_last_seen NULL).")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="Motif enregistré.")
    parser.add_argument("--concurrency", type=int, default=5, help="Blocages routeur en parallèle.")
    args = parser.parse_args()

    # Le routeur a sa propre borne (mikrotik_service), on reste sous le pool DB.
    s = get_settings()
    concurrency = max(1, min(args.concurrency, s.db_pool_size + s.db_max_overflow - 1))

    asyncio.run(run(
        args.dry_run, args.limit, args.reason, concurrency,
        args.min_days_down, args.include_never_seen,
    ))
