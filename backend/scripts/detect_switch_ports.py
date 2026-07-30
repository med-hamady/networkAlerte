r"""
Montre quel équipement supervisé est câblé sur quel port de switch.

À quoi ça sert
--------------
La surveillance des ports (port DOWN, port UP mais négocié sous 1 Gb/s) ne
regardait qu'UN port par switch : celui désigné par `uisp_switches.rocket_port_index`,
une colonne qu'aucun code ne renseignait — donc NULL partout, donc aucune de ces
deux alertes n'a jamais pu se déclencher sur aucun switch.

`switch_port_service` remplace la désignation manuelle par une détection : la
table d'apprentissage MAC du switch (BRIDGE-MIB) dit sur quel port chaque MAC a
été apprise, et nous connaissons déjà la MAC de chaque équipement supervisé.

Ce script exécute cette détection **sans rien écrire** et affiche, pour chaque
port trouvé, l'état et la vitesse actuellement lus en SNMP — c'est-à-dire
exactement ce qui déclencherait une alerte une fois le job actif. À lancer AVANT
de déployer, sur un vrai switch : le support BRIDGE-MIB dépend du firmware, et
un port légitimement à 100 Mb/s (s'il en existe) apparaîtra ici en clair plutôt
qu'en alerte critique WhatsApp à 3 h du matin.

Usage :

    # tous les switches, aucune écriture
    dc exec backend python scripts/detect_switch_ports.py

    # un seul switch
    dc exec backend python scripts/detect_switch_ports.py --switch-id 42

    # écrire réellement les attributions (ce que fait le job horaire)
    dc exec backend python scripts/detect_switch_ports.py --apply

Sans `--apply`, le script n'écrit RIEN (dry-run par défaut, volontairement).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.models.device import UispSwitch  # noqa: E402
from app.services import snmp_service, switch_port_service  # noqa: E402

# La logique vit dans `app/services/switch_port_service.py` : le job planifié et
# ce script doivent appliquer EXACTEMENT la même règle (un job ne peut pas
# importer depuis `scripts/`). Ici il ne reste que le pilotage en ligne de
# commande et l'affichage de l'état SNMP courant des ports trouvés.


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--switch-id", type=int, help="Ne traiter qu'un switch (par id).")
    parser.add_argument(
        "--apply", action="store_true",
        help="Écrit réellement les attributions. Sans ce drapeau, affichage seul.",
    )
    parser.add_argument(
        "--source", choices=("uisp", "fdb", "both"), default="both",
        help="uisp = data-links du contrôleur (la source qui fonctionne) ; "
             "fdb = table MAC du switch en SNMP (muette sur les switches UISP).",
    )
    args = parser.parse_args()

    settings = get_settings()
    async with async_session_factory() as session:
        common = dict(
            snmp_port=settings.snmp_port,
            snmp_timeout=settings.snmp_timeout,
            default_community=settings.snmp_default_community,
            dry_run=not args.apply,
        )
        results = []
        if args.source in ("uisp", "both"):
            results += await switch_port_service.detect_from_uisp(session, **common)
        if args.source in ("fdb", "both"):
            covered = {r.switch_id for r in results if r.attributed or r.error is None}
            results += await switch_port_service.detect_all(
                session, **common, switch_id=args.switch_id, skip_switch_ids=covered,
            )
        if args.switch_id is not None:
            results = [r for r in results if r.switch_id == args.switch_id]

        if not results:
            print("Aucun switch éligible (il faut une IP, et le statut 'up' "
                  "sauf si --switch-id est donné).")
            return 1

        total_ports = 0
        for wiring in results:
            print(f"\n=== {wiring.switch_name} (id={wiring.switch_id})  source={wiring.source}")
            if not wiring.ok:
                print(f"    ignoré : {wiring.error}")
                continue

            print(f"    {wiring.candidates} équipement(s) candidat(s)")
            if not wiring.attributed:
                print("    aucun port attribué — "
                      + ("UISP ne rend aucun lien ethernet pour ce switch"
                         if wiring.source == "uisp"
                         else "la FDB du switch ne rend aucune de nos MAC "
                              "(firmware sans BRIDGE-MIB, cas des switches UISP)"))
            for name, port in sorted(wiring.unwatched.items(), key=lambda kv: kv[1]):
                print(f"    port {port:>3}  {name:<40} non surveillé (UISP Power : "
                      f"100 Mbps est sa vitesse nominale ; down couvert par "
                      f"device_unreachable)")
            for port, names in wiring.rejected_ports.items():
                print(f"    port {port:>3}  REFUSÉ — annoncé par UISP mais absent de "
                      f"l'IF-MIB du switch : {', '.join(names)}")

            # État SNMP courant des ports trouvés = ce qui alerterait.
            switch = await session.get(UispSwitch, wiring.switch_id)
            metrics = {}
            if wiring.attributed and switch and switch.ip_address:
                metrics = await snmp_service.collect_switch_port_metrics(
                    host=switch.ip_address,
                    community=switch.snmp_community or settings.snmp_default_community,
                    port=settings.snmp_port,
                    timeout=settings.snmp_timeout,
                    max_ports=max(switch.max_ports, *wiring.attributed.values()),
                )

            min_speed = switch.port_min_speed_mbps if switch else 1000.0
            for name, port in sorted(wiring.attributed.items(), key=lambda kv: kv[1]):
                total_ports += 1
                up = metrics.get(f"port_{port}_up")
                speed = metrics.get(f"port_{port}_speed_mbps")
                if up is None:
                    verdict = "état non lu"
                elif up == 0.0:
                    verdict = "DOWN → alerterait (switch_port_down)"
                elif speed is not None and 0 < speed < min_speed:
                    verdict = f"UP {speed:.0f} Mbps → alerterait (switch_port_speed_low)"
                elif speed:
                    verdict = f"UP {speed:.0f} Mbps"
                else:
                    verdict = "UP"
                print(f"    port {port:>3}  {name:<40} {verdict}")

            for port, names in wiring.ambiguous.items():
                print(f"    port {port:>3}  IGNORÉ — {len(names)} équipements derrière "
                      f"(uplink/switch chaîné) : {', '.join(names)}")
            if wiring.max_ports_raised_to:
                print(f"    max_ports à relever à {wiring.max_ports_raised_to} "
                      f"(un équipement est au-delà de la plage scannée)")
            if wiring.rocket_port_index_set:
                print(f"    rocket_port_index renseigné : {wiring.rocket_port_index_set}")
            if wiring.unmatched:
                print(f"    non localisés ({len(wiring.unmatched)}) : "
                      f"{', '.join(wiring.unmatched)}")

        if args.apply:
            await session.commit()
            print(f"\n{total_ports} port(s) attribué(s) — ÉCRIT en base.")
        else:
            print(f"\n{total_ports} port(s) seraient attribués — DRY-RUN, rien écrit "
                  f"(relancer avec --apply).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
