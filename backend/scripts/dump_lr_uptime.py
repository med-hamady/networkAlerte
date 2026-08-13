"""
Exporte en JSON le nom, l'adresse IP, l'uptime et le statut de chaque LR abonné.

Lecture seule — le script ne touche à rien, il lit `lrs` + `device_metrics`.

Sortie : un TABLEAU d'objets à exactement QUATRE clés — `name`, `ip_address`,
`uptime_seconds`, `status`. C'est un contrat : ne rien y ajouter (ni site, ni
MAC, ni horodatage). Tout ce qui informe sur l'export lui-même (nombre de
lignes, exclusions) part sur **stderr**, donc `> fichier.json` ne récupère que
le document.

⚠️ **`status` a TROIS valeurs, pas deux** : `ONLINE` (`up`), `OFFLINE` (`down`)
et `UNKNOWN` — ce dernier étant un LR que plus rien ne mesure (IP libérée au
churn DHCP → hors du sweep de ping), et surtout PAS une panne constatée. Le
replier sur `OFFLINE` ferait remonter des coupures qui n'ont pas eu lieu.

⚠️ **Les LR « hors supervision » sont EXCLUS** (sans IP ET non vus par UISP
depuis `OUT_OF_SUPERVISION_DAYS`) : aucune source ne parle d'eux, donc leur
uptime ne peut être qu'un vestige d'avant le silence. Le compte des exclus est
annoncé sur stderr. La règle est importée de `schemas/device`, jamais recopiée.

⚠️ **L'uptime n'est pas mesuré à l'instant de l'export.** Il est écrit par les
jobs de poll en mode *collapse* (une seule ligne par (device, métrique), cf.
`persist_device_metrics`) : la valeur rendue est celle du DERNIER poll réussi.
Sur un LR tombé depuis, c'est donc l'uptime d'avant la panne. L'instant du
relevé n'est volontairement PAS exporté (format à 3 champs) — s'il fallait
distinguer une mesure fraîche d'un vestige, il faudrait le rajouter.

⚠️ **Un LR jamais pollé sort avec `uptime_seconds: null`**, jamais 0 : « pas de
mesure » n'est pas « vient de redémarrer ». `--skip-missing` les retire.

⚠️ **Deux clés selon la famille radio**, jamais la même — d'où la lecture des
deux, alors qu'une seule ressort :
  - `uptime_seconds` → airMAX 5AC (poll airOS direct), LiteBeam M5 (fan-out par
    l'AP ou SSH `wstalist`) : uptime de l'ÉQUIPEMENT (`host.uptime`).
  - `peer_uptime_s`  → LTU (fan-out depuis le Rocket parent) : uptime du PEER
    tel que l'AP le voit, c.-à-d. l'ancienneté du LIEN radio.
Ce ne sont pas tout à fait la même grandeur ; la première est préférée quand
les deux existent (elle décrit l'équipement, pas le lien). Le format à 3 champs
ne dit PAS laquelle a servi : sur un LTU, l'uptime rendu est celui du lien.

Usage (dans le conteneur backend) :
    dc exec -T backend python scripts/dump_lr_uptime.py > lr_uptime.json
    dc exec backend python scripts/dump_lr_uptime.py --out /app/lr_uptime.json
    dc exec -T backend python scripts/dump_lr_uptime.py --skip-missing > lr_uptime.json
"""

import argparse
import asyncio
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.device import Lr
from app.models.device_metric import DeviceMetric

# La règle « hors supervision » est IMPORTÉE, jamais recopiée : elle est déjà
# écrite deux fois (ici en Python pour la fiche, et en SQL dans
# `fn_access_clients`, migration cc3d4e5f6a7b) et les deux doivent rester
# d'accord. Une troisième copie divergerait au premier ajustement du délai.
from app.schemas.device import is_out_of_supervision

# Par ordre de PRÉFÉRENCE : l'uptime de l'équipement l'emporte sur celui du lien.
_UPTIME_KEYS = ("uptime_seconds", "peer_uptime_s")

# `devices.status` porte TROIS valeurs, pas deux. « unknown » n'est pas une
# panne : c'est un LR que plus rien ne mesure (IP libérée au churn DHCP → il
# sort du sweep de ping). Le rendre OFFLINE affirmerait une coupure que
# personne n'a constatée — exactement la lecture fausse que le badge ambre de
# /access a été créé pour éviter. Il sort donc sous son propre libellé.
_STATUS_LABELS = {"up": "ONLINE", "down": "OFFLINE"}
_STATUS_FALLBACK = "UNKNOWN"


async def _latest_uptimes(session) -> dict[int, tuple[str, float, datetime.datetime]]:
    """{device_id: (clé, valeur, instant)} — la meilleure clé disponible par LR.

    Le collapse garantit déjà une ligne par (device, métrique), mais on trie
    quand même par `collected_at` : si la politique de persistance changeait,
    ce script continuerait de rendre la valeur la plus récente.
    """
    stmt = (
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            DeviceMetric.metric_value,
            DeviceMetric.collected_at,
        )
        # Jointure sur la TABLE `lrs` (pas sur la classe) : on ne veut que les
        # LR abonnés, sans traîner l'héritage joined-table dans la requête.
        .join(Lr.__table__, Lr.__table__.c.id == DeviceMetric.device_id)
        .where(DeviceMetric.metric_name.in_(_UPTIME_KEYS))
        .order_by(DeviceMetric.device_id, DeviceMetric.collected_at)
    )
    best: dict[int, tuple[str, float, datetime.datetime]] = {}
    for dev_id, name, value, collected_at in (await session.execute(stmt)).all():
        current = best.get(dev_id)
        if current is None:
            best[dev_id] = (name, value, collected_at)
            continue
        # Priorité à la clé la mieux placée dans _UPTIME_KEYS ; à clé égale,
        # au relevé le plus récent.
        better_key = _UPTIME_KEYS.index(name) < _UPTIME_KEYS.index(current[0])
        fresher = name == current[0] and collected_at >= current[2]
        if better_key or fresher:
            best[dev_id] = (name, value, collected_at)
    return best


async def run(out_path: str | None, skip_missing: bool) -> None:
    async with async_session_factory() as session:
        uptimes = await _latest_uptimes(session)
        lrs = (await session.execute(select(Lr).order_by(Lr.name))).scalars().all()

        rows = []
        excluded = 0
        for lr in lrs:
            # « Hors supervision » = sans IP ET invisible pour UISP depuis
            # `OUT_OF_SUPERVISION_DAYS` : les deux sources se taisent. Un uptime
            # exporté pour un tel abonné ne pourrait être qu'un vestige d'avant
            # le silence — il se lirait comme une mesure. Exclu, et COMPTÉ :
            # une exclusion silencieuse ferait passer l'export pour exhaustif.
            if is_out_of_supervision(lr.ip_address, lr.uisp_last_seen):
                excluded += 1
                continue
            found = uptimes.get(lr.id)
            if found is None and skip_missing:
                continue
            _key, value, _collected_at = found if found else (None, None, None)
            # QUATRE clés, jamais une de plus : ce JSON est un contrat.
            rows.append({
                "name": lr.name,
                "ip_address": lr.ip_address,
                "uptime_seconds": value,
                "status": _STATUS_LABELS.get(lr.status, _STATUS_FALLBACK),
            })

    text = json.dumps(rows, ensure_ascii=False, indent=2)

    # Les comptes vont sur STDERR, jamais dans le JSON : ils restent lisibles à
    # l'exécution sans polluer un document qui ne doit porter que les 3 champs.
    with_uptime = sum(1 for r in rows if r["uptime_seconds"] is not None)
    summary = (
        f"{len(rows)} LR ({with_uptime} avec uptime, "
        f"{excluded} hors supervision exclus)."
    )

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"{summary} Écrit dans {out_path}.", file=sys.stderr)
    else:
        print(text)
        print(summary, file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Exporte nom / IP / uptime de chaque LR abonné en JSON.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Fichier de sortie (défaut : stdout, pour être redirigé).",
    )
    parser.add_argument(
        "--skip-missing", action="store_true",
        help="N'inclut que les LR dont un uptime a été relevé.",
    )
    args = parser.parse_args()

    asyncio.run(run(args.out, args.skip_missing))
