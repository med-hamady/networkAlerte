"""
Enregistrer en base des coupures DÉJÀ POSÉES sur le routeur.

À quoi ça sert
--------------
Des règles drop existent sur le routeur pour des abonnés que notre base croit
actifs — posées par le système historique (``add_rules.php``), à la main dans
Winbox, ou par nous avant une restauration de base. Tant que la base l'ignore,
ces clients apparaissent « en ligne » partout dans le dashboard alors qu'ils sont
coupés, et l'écart ne se répare jamais tout seul : ``_reconcile_router`` ne parle
au routeur que sur *transition*, donc deux états déjà « d'accord » (client actif,
aucune règle connue) ne déclenchent aucun appel.

Ce script aligne la BASE sur le routeur — l'inverse du sens habituel. Il ne pose
aucune règle et n'ouvre aucune session SSH.

⚠️ Il VÉRIFIE le routeur avant d'écrire, et ce n'est pas une précaution de confort
------------------------------------------------------------------------------
Poser ``router_blocked=True`` sur une MAC dont le routeur ne porte AUCUNE règle
produirait le pire état possible : ``desired_router_block()`` vaudrait True,
``lr.router_blocked`` aussi, donc la réconciliation les verrait alignés et **ne
poserait jamais la règle**. Le client serait libre pendant que toute l'interface
le dit coupé, indéfiniment. Le script lit donc les règles du routeur (une seule
session API pour tout le lot) et refuse d'écrire pour une MAC qu'il n'y trouve
pas — sauf ``--post-missing``, qui pose alors réellement la règle manquante.

État écrit (identique à ``block_out_of_supervision``, pour que la suite du
système traite ces clients comme n'importe quel autre bloqué) :

  - ``client_blocked=True`` + ``client_blocked_at`` + ``client_blocked_reason``
  - ``router_blocked=True`` + ``router_blocked_at``
  - ``client_block_enforced_at`` reste NULL

Conséquence à connaître : ``desired_router_block(lr)`` devient vrai, donc le job
de renforcement **maintient** la règle du routeur. Et si le LR est joignable en
SSH, ce même job basculera la coupure sur l'équipement du client puis retirera la
règle du routeur — convergence normale du système, pas un effet de bord de ce
script. Un ``unblock`` (API ou dashboard) lève tout, comme pour tout autre
blocage.

Usage (dans le conteneur backend) :
    dc exec backend python scripts/mark_router_blocked.py --dry-run 78:45:58:0B:BC:A1 ...
    dc exec backend python scripts/mark_router_blocked.py --file macs.txt
    dc exec backend python scripts/mark_router_blocked.py --reason "Impayé" 78:45:58:0B:BC:A1
    dc exec backend python scripts/mark_router_blocked.py --post-missing <mac>
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import async_session_factory
from app.services import client_block_service, fai_audit, mikrotik_service
from app.services.client_block_service import normalize_mac

DEFAULT_REASON = "Coupé sur le routeur — régularisation de la base"


def _read_macs(args) -> list[str]:
    """MAC de la ligne de commande + du fichier, dédoublonnées, dans l'ordre."""
    raw = list(args.macs)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            raw += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    seen: set[str] = set()
    macs: list[str] = []
    for value in raw:
        if value.lower() not in seen:
            seen.add(value.lower())
            macs.append(value)
    return macs


async def run(macs: list[str], reason: str, dry_run: bool, post_missing: bool) -> None:
    if not mikrotik_service.is_enabled():
        print("MikroTik désactivé (MIKROTIK_ENABLED / mot de passe) — impossible de "
              "vérifier les règles, donc rien ne sera écrit.")
        return

    # UNE session API pour tout le lot : le routeur n'est pas interrogé par MAC.
    rules, error = await mikrotik_service.list_client_block_rules()
    if error:
        print(f"Lecture du routeur impossible : {error}")
        print("Rien n'est écrit — une base alignée sur une lecture ratée serait pire "
              "que l'écart actuel.")
        return
    on_router = {rule["mac"].lower(): rule for rule in rules}
    print(f"{len(rules)} règle(s) de coupure lue(s) sur le routeur.\n")

    written, skipped, missing, unknown, invalid = 0, 0, 0, 0, 0

    for value in macs:
        try:
            mac = normalize_mac(value)
        except ValueError:
            print(f"  [MAC INVALIDE] {value}")
            invalid += 1
            continue

        async with async_session_factory() as session:
            lr = await client_block_service.find_lr_by_mac(session, mac)
            if lr is None:
                # Rien à écrire : l'état de blocage vit sur la fiche du LR. Une MAC
                # hors inventaire reste coupée par le routeur, simplement non suivie.
                print(f"  [HORS INVENTAIRE] {mac} — aucun LR en base, rien à marquer")
                unknown += 1
                continue

            rule = on_router.get(mac.lower())
            if rule is None:
                if not post_missing:
                    print(f"  [PAS DE RÈGLE]   {mac}  {lr.name} — le routeur ne coupe PAS ce "
                          f"client ; refus de le marquer bloqué (--post-missing pour poser "
                          f"la règle)")
                    missing += 1
                    continue
                if dry_run:
                    print(f"  [POSERAIT]       {mac}  {lr.name} — règle absente, serait posée")
                else:
                    ok, msg = await mikrotik_service.block_by_mac(
                        mac, mikrotik_service.build_comment(f"regularisation {lr.name}"),
                    )
                    if not ok:
                        print(f"  [ÉCHEC ROUTEUR]  {mac}  {lr.name} : {msg}")
                        missing += 1
                        continue
                    print(f"  [RÈGLE POSÉE]    {mac}  {lr.name}")
            elif rule["disabled"]:
                # Une règle désactivée ne coupe personne : la traiter comme une
                # coupure effective inscrirait un blocage qui n'existe pas.
                print(f"  [RÈGLE INACTIVE] {mac}  {lr.name} — règle présente mais DÉSACTIVÉE, "
                      f"elle ne coupe rien ; non marqué")
                missing += 1
                continue

            if lr.client_blocked and lr.router_blocked:
                print(f"  [DÉJÀ À JOUR]    {mac}  {lr.name}")
                skipped += 1
                continue

            if dry_run:
                print(f"  [MARQUERAIT]     {mac}  {lr.name}  (site {lr.site or '—'})")
                written += 1
                continue

            if not lr.client_blocked:
                lr.client_blocked = True
                lr.client_blocked_at = client_block_service._now()
            lr.client_blocked_reason = reason
            lr.router_blocked = True
            lr.router_blocked_at = client_block_service._now()
            await session.commit()

            fai_audit.log_action(
                "ROUTER_BLOCK", ok=True, mac=mac, name=lr.name, mode=lr.block_mode,
                source="script", message=f"Coupure routeur préexistante enregistrée : {reason}",
            )
            print(f"  [MARQUÉ]         {mac}  {lr.name}  (site {lr.site or '—'})")
            written += 1

    print("\n" + "=" * 70)
    verb = "seraient marqués" if dry_run else "marqués"
    print(f"{verb} : {written}")
    print(f"déjà à jour     : {skipped}")
    print(f"sans règle      : {missing}")
    print(f"hors inventaire : {unknown}")
    if invalid:
        print(f"MAC invalides   : {invalid}")
    print("=" * 70)
    if dry_run:
        print("DRY-RUN — aucune écriture. Relancer sans --dry-run pour appliquer.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enregistre en base des coupures déjà posées sur le routeur."
    )
    parser.add_argument("macs", nargs="*", help="MAC à marquer (toute notation).")
    parser.add_argument("--file", help="Fichier de MAC, une par ligne (# = commentaire).")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="Motif enregistré en base.")
    parser.add_argument("--dry-run", action="store_true", help="Prévisualiser sans écrire.")
    parser.add_argument(
        "--post-missing", action="store_true",
        help="Poser la règle sur le routeur quand elle manque, au lieu de refuser.",
    )
    args = parser.parse_args()

    macs = _read_macs(args)
    if not macs:
        parser.error("aucune MAC fournie (arguments ou --file).")

    asyncio.run(run(macs, args.reason, args.dry_run, args.post_missing))
