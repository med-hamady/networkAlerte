r"""Combien de Rockets — et donc de clients — peut-on encore installer ?

À quoi ça sert
--------------
Deux questions de dimensionnement, posées ensemble parce que la seconde borne
la première :

1. **Capacité clients du parc** — ce que portent les Rockets DÉJÀ posés.
2. **Place restante sur les mâts** — un site accepte au plus `--max-per-site`
   équipements (16). Ce qui reste est le nombre de Rockets qu'on peut encore y
   poser, et donc les clients qu'on pourrait servir en plus.

⚠️ Les deux plafonds ne disent PAS la même chose. Le premier est la capacité
**installée**, le second la capacité **ajoutable**. Un parc peut avoir de la
capacité libre partout et plus une seule place sur les mâts — ou l'inverse.

⚠️ Deux barèmes, jamais un seul
--------------------------------
Un Rocket **LTU** porte `--clients-per-ltu` clients (30), un Rocket **airMAX**
`--clients-per-airmax` (23). La famille est lue dans `rockets.radio_tech`, la
même colonne que celle qui règle déjà les seuils de saturation — un barème
unique surestimerait le parc de 7 clients par Rocket airMAX.

⚠️ Ce qui occupe une place dans les 16
---------------------------------------
Les **Rockets** et les **PTP LiteBeam** : les radios posées sur le mât, celles
qui prennent la place. Sont exclus, à la demande :

* les **airFiber / AF60** (backhauls 60 GHz),
* les **switches**,
* les **UISP Power**.

Les **LR sont exclus par nature** : ce sont les antennes des abonnés, chez eux,
pas sur notre mât.

⚠️ Les clients ajoutables sont donnés SOUS LES DEUX HYPOTHÈSES (site équipé en
LTU / en airMAX), jamais en un seul chiffre : le script ne sait pas quelle
famille sera posée sur une place libre, et trancher à sa place ferait passer
une hypothèse pour une prévision.

⚠️ Le `site` lu est la colonne dénormalisée `devices.site`, la même que celle
du rapport de capacité infra quotidien — deux comptages du même parc ne
doivent pas pouvoir diverger.

Deux sources, un seul comptage
------------------------------
Par défaut le script lit la BASE (il tourne alors dans le conteneur backend).
Avec `--api`, il interroge `/devices` à la place — pour répondre depuis un
poste qui n'a ni accès SSH ni accès PostgreSQL mais qui joint nginx sur le LAN.
Les deux sources alimentent exactement le même comptage et le même rendu.

Usage
-----
    python scripts/site_rocket_headroom.py                       # source = base
    python scripts/site_rocket_headroom.py --api https://10.135.3.25 --key <API_KEY>
    python scripts/site_rocket_headroom.py --clients-per-ltu 30 --clients-per-airmax 23
    python scripts/site_rocket_headroom.py --count-ptp-as-free   # PTP hors des 16
    python scripts/site_rocket_headroom.py --json > capacite.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.models.device import Device, Rocket  # noqa: E402

# Un Rocket est éclaté en DEUX espèces dès le comptage : leur capacité diffère,
# et les recoller en « rocket » obligerait à re-séparer plus loin.
LTU, AIRMAX, PTP = "rocket_ltu", "rocket_airmax", "ptp_litebeam"

# Ce qui occupe une place sur le mât. `airfiber`, `uisp_switch` et `uisp_power`
# en sont volontairement absents (cf. docstring).
MAST_TYPES = (LTU, AIRMAX, PTP)

# Les espèces qui SERVENT des clients. Un PTP LiteBeam prend une place mais ne
# sert aucun abonné — il n'est donc pas ici.
SERVING_TYPES = (LTU, AIRMAX)

CLIENT_TYPE = "lr"
_NO_SITE = "(sans site)"


def _species(device_type: str, radio_tech: str | None) -> str:
    """Type de comptage d'un équipement.

    ⚠️ Un Rocket sans `radio_tech` connu est rangé en airMAX, l'hypothèse
    BASSE : sur un inventaire incomplet, mieux vaut sous-annoncer la capacité
    que promettre des places qui n'existent pas.
    """
    if device_type != "rocket":
        return device_type
    return LTU if (radio_tech or "").lower() == "ltu" else AIRMAX


async def rows_from_db(session) -> list[tuple[str | None, str, int]]:
    """(site, espèce, n) depuis la base, en une seule requête agrégée.

    La jointure passe par les TABLES et non par la classe `Rocket` : on veut un
    `LEFT JOIN` plat sur `rockets`, pas un chargement polymorphe des sous-classes
    (qui rapatrierait les lignes au lieu de les compter en SQL).
    """
    devices, rockets = Device.__table__, Rocket.__table__
    stmt = (
        select(devices.c.site, devices.c.device_type, rockets.c.radio_tech,
               func.count())
        .select_from(devices.outerjoin(rockets, rockets.c.id == devices.c.id))
        .group_by(devices.c.site, devices.c.device_type, rockets.c.radio_tech)
    )
    return [
        (site, _species(dtype, tech), n)
        for site, dtype, tech, n in (await session.execute(stmt)).all()
    ]


async def rows_from_api(base_url: str, api_key: str
                        ) -> list[tuple[str | None, str, int]]:
    """(site, espèce, n) depuis `/devices`, paginé.

    ⚠️ `limit` est plafonné à 1000 côté API et le parc en compte davantage : on
    pagine jusqu'à une page courte. Sans ça le comptage serait SILENCIEUSEMENT
    tronqué à la première page — un sous-comptage qui ressemble à un résultat.
    """
    page = 1000
    counts: dict[tuple[str | None, str], int] = {}
    seen = 0
    # verify=False : le certificat du serveur est auto-signé (cf. nginx/certs).
    async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
        skip = 0
        while True:
            resp = await client.get(
                f"{base_url.rstrip('/')}/api/v1/devices",
                params={"skip": skip, "limit": page},
                headers={"X-API-Key": api_key},
            )
            resp.raise_for_status()
            batch = resp.json()
            for dev in batch:
                key = (dev.get("site"),
                       _species(dev.get("device_type") or "?",
                                dev.get("radio_tech")))
                counts[key] = counts.get(key, 0) + 1
            seen += len(batch)
            if len(batch) < page:
                break
            skip += page
    print(f"  ({seen} équipements lus depuis l'API)", file=sys.stderr)
    return [(site, sp, n) for (site, sp), n in counts.items()]


def tally(rows, max_per_site: int, count_ptp: bool) -> dict:
    """Compte par site et pour le parc entier, quelle que soit la source."""
    sites: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for site, species, n in rows:
        totals[species] = totals.get(species, 0) + n
        if species not in MAST_TYPES:
            continue
        key = " ".join(site.split()) if site else _NO_SITE
        bucket = sites.setdefault(key, dict.fromkeys(MAST_TYPES, 0))
        bucket[species] += n

    occupied = MAST_TYPES if count_ptp else SERVING_TYPES

    out = []
    for name, bucket in sites.items():
        used = sum(bucket[t] for t in occupied)
        # Un site DÉJÀ au-delà du plafond ne rend pas une place négative : il
        # rend zéro place ET son dépassement, qui est une information à part —
        # on ne peut pas désinstaller pour gagner de la capacité.
        out.append({
            "site": name,
            "rockets_ltu": bucket[LTU],
            "rockets_airmax": bucket[AIRMAX],
            "ptp_litebeam": bucket[PTP],
            "occupied": used,
            "free_slots": max(0, max_per_site - used),
            "over_capacity": max(0, used - max_per_site),
        })

    out.sort(key=lambda r: (-r["free_slots"], r["site"]))
    return {"sites": out, "totals": totals}


def render(data: dict, per_ltu: int, per_airmax: int, max_per_site: int,
           as_json: bool) -> None:
    totals, sites = data["totals"], data["sites"]

    n_ltu = totals.get(LTU, 0)
    n_airmax = totals.get(AIRMAX, 0)
    n_rockets = n_ltu + n_airmax
    cap_ltu, cap_airmax = n_ltu * per_ltu, n_airmax * per_airmax
    capacity = cap_ltu + cap_airmax
    clients = totals.get(CLIENT_TYPE, 0)
    free_capacity = capacity - clients

    for row in sites:
        row["extra_clients_if_ltu"] = row["free_slots"] * per_ltu
        row["extra_clients_if_airmax"] = row["free_slots"] * per_airmax

    extra_rockets = sum(r["free_slots"] for r in sites)

    if as_json:
        print(json.dumps({
            "clients_per_ltu": per_ltu,
            "clients_per_airmax": per_airmax,
            "max_per_site": max_per_site,
            "total_rockets": n_rockets,
            "rockets_ltu": n_ltu,
            "rockets_airmax": n_airmax,
            "total_capacity_clients": capacity,
            "capacity_from_ltu": cap_ltu,
            "capacity_from_airmax": cap_airmax,
            "used_capacity_clients": clients,
            "free_capacity_clients": free_capacity,
            "extra_rockets_installable": extra_rockets,
            "extra_clients_if_all_ltu": extra_rockets * per_ltu,
            "extra_clients_if_all_airmax": extra_rockets * per_airmax,
            "inventory": totals,
            "sites": sites,
        }, ensure_ascii=False, indent=2))
        return

    pct = (clients / capacity * 100) if capacity else 0.0
    print("=" * 78)
    print("CAPACITE CLIENTS DU PARC")
    print("=" * 78)
    print(f"  Rockets LTU      : {n_ltu:>5}  x {per_ltu:<3} = {cap_ltu:>6} clients")
    print(f"  Rockets airMAX   : {n_airmax:>5}  x {per_airmax:<3} = {cap_airmax:>6} clients")
    print(f"  {'-' * 46}")
    print(f"  TOTAL ROCKETS    : {n_rockets:>5}")
    print()
    print(f"  Capacite totale  : {capacity:>6} clients")
    print(f"  Capacite utilisee: {clients:>6} clients   ({pct:.1f} %)")
    print(f"  CAPACITE LIBRE   : {free_capacity:>6} clients")

    print()
    print("=" * 78)
    print(f"PLACE RESTANTE SUR LES MATS (max {max_per_site} equipements par site)")
    print("=" * 78)
    print(f"  {'Site':<14}{'LTU':>5}{'aMAX':>6}{'PTP':>5}{'Occupe':>8}"
          f"{'Libre':>7}{'+cli LTU':>10}{'+cli aMAX':>11}")
    print("  " + "-" * 74)
    for r in sites:
        flag = f"  (+{r['over_capacity']} au-dela)" if r["over_capacity"] else ""
        print(f"  {r['site']:<14}{r['rockets_ltu']:>5}{r['rockets_airmax']:>6}"
              f"{r['ptp_litebeam']:>5}{r['occupied']:>8}{r['free_slots']:>7}"
              f"{r['extra_clients_if_ltu']:>10}{r['extra_clients_if_airmax']:>11}{flag}")
    print("  " + "-" * 74)
    print(f"  {'TOTAL':<14}{n_ltu:>5}{n_airmax:>6}"
          f"{totals.get(PTP, 0):>5}"
          f"{sum(r['occupied'] for r in sites):>8}{extra_rockets:>7}"
          f"{extra_rockets * per_ltu:>10}{extra_rockets * per_airmax:>11}")

    print()
    print(f"  => {extra_rockets} Rockets installables sur les sites actuels.")
    print(f"     Soit {extra_rockets * per_airmax} a {extra_rockets * per_ltu} clients de plus,")
    print("     selon la famille posee (airMAX ... LTU).")
    print(f"  => Avec les {free_capacity} places libres sur les Rockets deja poses :")
    print(f"     {free_capacity + extra_rockets * per_airmax} a "
          f"{free_capacity + extra_rockets * per_ltu} clients de plus au total.")

    print()
    print("  Inventaire complet (tous types) :")
    for species, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"    {species:<16} {n:>6}")


async def main() -> int:
    p = argparse.ArgumentParser(
        description="Capacite clients (LTU x30 / airMAX x23) + place sur les mats.")
    p.add_argument("--clients-per-ltu", type=int, default=30)
    p.add_argument("--clients-per-airmax", type=int, default=23)
    p.add_argument("--max-per-site", type=int, default=16)
    p.add_argument("--count-ptp-as-free", action="store_true",
                   help="ne PAS compter les PTP LiteBeam dans les 16 "
                        "(par defaut ils occupent une place, comme un Rocket)")
    p.add_argument("--api", metavar="URL",
                   help="lire /devices au lieu de la base (ex. https://10.135.3.25)")
    p.add_argument("--key", metavar="API_KEY", help="cle X-API-Key, avec --api")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.api:
        if not args.key:
            print("--api exige --key (la cle X-API-Key de la prod)", file=sys.stderr)
            return 2
        rows = await rows_from_api(args.api, args.key)
    else:
        async with async_session_factory() as session:
            rows = await rows_from_db(session)

    data = tally(rows, args.max_per_site, count_ptp=not args.count_ptp_as_free)
    render(data, args.clients_per_ltu, args.clients_per_airmax,
           args.max_per_site, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
