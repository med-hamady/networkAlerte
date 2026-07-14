"""
Blocage de masse de clients par MAC — depuis un CSV.

Lit un CSV (`"idclient","info","mac","statu"`), résout chaque MAC vers son LR
(table `lrs`, identité = MAC, la même que la découverte / le sync UISP) et
applique le blocage internet via `client_block_service.block_client` :

  - l'intention de blocage est **persistée en base** (`lrs.client_blocked=True`,
    `block_mode`, `client_blocked_at`, `client_blocked_reason`) — c'est la mise
    à jour du statut dans notre base ;
  - une tentative d'**enforcement SSH immédiate** est faite sur le LR ; si le LR
    est injoignable, l'intention reste posée et le `client_block_enforcement_job`
    (toutes les 120 s) ré-appliquera automatiquement.

Le champ `statu` du CSV (système de facturation, `2` = à couper) n'est pas
copié tel quel : notre statut à nous est `client_blocked`. Toutes les lignes du
fichier sont traitées comme « à bloquer ».

Usage (dans le conteneur backend) :
    dc exec backend python scripts/block_clients.py scripts/clients_to_block.csv --dry-run
    dc exec backend python scripts/block_clients.py scripts/clients_to_block.csv
    dc exec backend python scripts/block_clients.py scripts/clients_to_block.csv --mode whatsapp_only

Options :
    --dry-run          Résout les MAC et affiche ce qui serait bloqué, sans rien écrire.
    --mode MODE        `full` | `whatsapp_only` (défaut : CLIENT_BLOCK_DEFAULT_MODE).
    --reason TEXTE     Motif enregistré sur le blocage (défaut : "Blocage de masse (impayé)").
    --concurrency N    Blocages SSH menés en parallèle (défaut 8 ; pool DB = 15 max).
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

DEFAULT_REASON = "Blocage de masse (impayé)"


def _load_targets(path: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Parse the CSV → (valid, invalid).

    Returns two lists of (mac_or_raw, label). ``valid`` holds normalised MACs,
    de-duplicated (a MAC repeated in the file is blocked once); ``invalid`` holds
    rows whose MAC can't be parsed (e.g. ``pending-1503``).
    """
    valid: list[tuple[str, str]] = []
    invalid: list[tuple[str, str]] = []
    seen: set[str] = set()

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw = (row.get("mac") or "").strip()
            label = (row.get("info") or "").strip() or (row.get("idclient") or "?")
            if not raw:
                continue
            try:
                mac = normalize_mac(raw)
            except ValueError:
                invalid.append((raw, label))
                continue
            if mac in seen:
                continue
            seen.add(mac)
            valid.append((mac, label))

    return valid, invalid


async def _block_one(
    sem: asyncio.Semaphore,
    mac: str,
    label: str,
    mode: str | None,
    reason: str,
    dry_run: bool,
    results: dict[str, list],
) -> None:
    """Resolve one MAC to its LR and block it (own session, safe under gather)."""
    async with sem:
        async with async_session_factory() as session:
            lr = await client_block_service.find_lr_by_mac(session, mac)
            if lr is None:
                results["not_found"].append((mac, label))
                return
            if dry_run:
                results["would_block"].append((mac, label, lr.name))
                return
            try:
                ok, msg = await client_block_service.block_client(
                    session, lr, reason=reason, mode=mode
                )
            except Exception as exc:  # never let one LR abort the batch
                await session.rollback()
                results["pending"].append((mac, label, lr.name, f"exception: {exc}"))
                return
            # Même journal que les ordres du système de paiement — la migration de
            # masse est justement ce qu'on voudra pouvoir relire client par client.
            fai_audit.log_action(
                "BLOCK", ok=ok, mac=lr.mac_address, name=lr.name,
                mode=lr.block_mode, source="script", message=msg,
            )
            if ok:
                results["enforced"].append((mac, label, lr.name))
            else:
                results["pending"].append((mac, label, lr.name, msg))


def _print_report(results: dict[str, list], invalid: list, dry_run: bool) -> None:
    print("\n" + "=" * 60)
    if dry_run:
        would = results["would_block"]
        print(f"DRY-RUN — {len(would)} client(s) seraient bloqués :")
        for mac, label, name in would:
            print(f"  [MATCH] {mac}  {label}  → LR {name}")
    else:
        enforced = results["enforced"]
        pending = results["pending"]
        print(f"Bloqués + appliqués SSH : {len(enforced)}")
        print(f"Bloqués mais NON appliqués (le job réessaiera) : {len(pending)}")
        for mac, label, name, msg in pending:
            print(f"  [PENDING] {mac}  {label}  → LR {name} : {msg}")

    not_found = results["not_found"]
    if not_found:
        print(f"\nMAC introuvable en base (aucun LR) : {len(not_found)}")
        for mac, label in not_found:
            print(f"  [NOT FOUND] {mac}  {label}")

    if invalid:
        print(f"\nMAC invalide (ignorée) : {len(invalid)}")
        for raw, label in invalid:
            print(f"  [INVALID] {raw!r}  {label}")
    print("=" * 60)


async def run(path: str, mode: str | None, reason: str, dry_run: bool, concurrency: int) -> None:
    valid, invalid = _load_targets(path)
    resolved_mode = client_block_service._resolve_mode(mode)
    print(
        f"{len(valid)} MAC valide(s), {len(invalid)} invalide(s). "
        f"Mode={resolved_mode}. {'DRY-RUN' if dry_run else 'APPLICATION'}."
    )

    results: dict[str, list] = {
        "enforced": [], "pending": [], "not_found": [], "would_block": [],
    }
    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(
        *(
            _block_one(sem, mac, label, mode, reason, dry_run, results)
            for mac, label in valid
        )
    )
    _print_report(results, invalid, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blocage de masse de clients par MAC (CSV).")
    parser.add_argument("csv_path", help="Chemin du CSV (colonnes idclient,info,mac,statu).")
    parser.add_argument("--dry-run", action="store_true", help="Prévisualiser sans écrire.")
    parser.add_argument(
        "--mode", choices=list(client_block_service.VALID_MODES), default=None,
        help="Mode de blocage (défaut : CLIENT_BLOCK_DEFAULT_MODE).",
    )
    parser.add_argument("--reason", default=DEFAULT_REASON, help="Motif du blocage.")
    parser.add_argument("--concurrency", type=int, default=8, help="Blocages SSH en parallèle.")
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"Fichier introuvable : {args.csv_path}")
        sys.exit(1)

    # Bound concurrency to the DB pool (pool_size + max_overflow) — block_client
    # holds its session across the SSH round-trip.
    s = get_settings()
    pool_cap = s.db_pool_size + s.db_max_overflow
    concurrency = max(1, min(args.concurrency, pool_cap - 1))

    asyncio.run(run(args.csv_path, args.mode, args.reason, args.dry_run, concurrency))
