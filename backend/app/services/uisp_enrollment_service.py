"""Enrôlement d'un CPE abonné dans UISP — pose de la clé du contrôleur par SSH.

Le problème
-----------
Un LR ne remonte dans l'inventaire UISP que s'il porte la « clé » du contrôleur
(une chaîne de connexion `wss://…`, cf. `ssh_service.set_uisp_key`). Un abonné
branché, actif et facturable qui ne l'a pas est **invisible de l'inventaire** —
c'est exactement la liste « découverts par radio mais absents de UISP » de la
page Diagnostics d'accès (`access_diagnostics_service`). Jusqu'ici cette liste
ne pouvait être régularisée qu'à la main, équipement par équipement.

Ce que fait ce service
----------------------
Il pousse la clé sur un LR (ou sur toute la liste) et **constate l'adoption sur
l'équipement lui-même** avant de compter un succès. Deux garde-fous repris du
blocage client, pour les mêmes raisons :

  - **identité MAC** (`ssh_service.identity_refusal`) : la fiche cible une MAC
    mais la session part sur une IP que le DHCP a pu redonner. Enrôler le
    mauvais abonné l'inscrirait à la place d'un autre dans l'inventaire.
  - **concurrence SSH bornée** : au-delà d'environ 150 poignées de main
    simultanées les radios décrochent (terrain 2026-06-16). Une régularisation
    de masse porte sur des centaines de LR — sans plafond elle se saborde.

Ce qu'il ne fait PAS
--------------------
Aucune boucle d'enforcement, contrairement au blocage client. Un enrôlement est
un acte **ponctuel** : une fois adopté, le contrôleur remplace notre clé par une
clé propre à l'équipement, et la réappliquer périodiquement le dé-enrôlerait à
chaque passage. `set_uisp_key` est donc idempotent par abstention — il ne touche
à rien si `unms-conn-status` vaut déjà "1".
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.services import ssh_service

logger = logging.getLogger(__name__)

# Même plafond que le blocage client, pour la même raison : la radio sature bien
# avant nous. Une régularisation de masse est une opération de fond, pas une
# course — la lenteur est ici une fonctionnalité.
_SSH_CONCURRENCY = asyncio.Semaphore(10)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def enrollment_available() -> bool:
    """La clé du contrôleur est-elle configurée ? (sinon rien n'est possible)"""
    return bool(get_settings().uisp_device_key.strip())


async def enroll_lr(
    session: AsyncSession, lr: Lr, force: bool = False
) -> tuple[bool, str]:
    """Pose la clé UISP sur un LR et attend son adoption par le contrôleur.

    Retourne ``(ok, message)`` où ``ok`` signifie **adopté**, pas « fichier
    écrit » : poser une clé sans vérifier ne prouverait rien, et laisserait
    croire à une régularisation qui n'a pas eu lieu.

    Sans effet sur un équipement déjà provisionné pour ce contrôleur (sa clé lui
    appartient désormais), sauf ``force=True`` — réservé au cas de la clé
    orpheline, cf. `ssh_service.set_uisp_key`.

    Effets de bord persistés en cas de succès : `uisp_enrolled_at`, plus les deux
    auto-réparations habituelles des chemins SSH (épinglage de la clé d'hôte au
    premier contact, promotion du mot de passe de repli qui a fonctionné).
    """
    settings = get_settings()
    key = settings.uisp_device_key.strip()
    if not key:
        return False, (
            "Aucune clé UISP configurée. Renseigner UISP_DEVICE_KEY (UISP → "
            "Paramètres → Équipements → clé UISP) avant d'enrôler."
        )
    if not (lr.ssh_username and lr.ssh_password):
        return False, (
            f"Le LR {lr.name} n'a pas d'identifiants SSH — impossible de poser la "
            f"clé. Configure ssh_username/ssh_password via PUT /api/v1/devices/{lr.id}."
        )
    if not lr.ip_address:
        # Sans IP le LR est hors du sweep de ping ET injoignable en SSH. Le dire
        # explicitement évite un « échec de connexion » qui ferait chercher une
        # panne réseau là où il n'y a qu'une fiche sans adresse.
        return False, (
            f"Le LR {lr.name} n'a pas d'adresse IP connue (hors supervision) — "
            f"rien à joindre. Il sera enrôlable dès qu'un AP le rapportera."
        )

    primary_pw = lr.ssh_password
    async with _SSH_CONCURRENCY:
        ok, msg, observed_fp, used_pw = await ssh_service.set_uisp_key(
            host=lr.ip_address,
            port=lr.ssh_port or 22,
            username=lr.ssh_username,
            password=primary_pw,
            key_uri=key,
            ui_url=settings.uisp_base_url.strip(),
            expected_fingerprint=lr.ssh_host_fingerprint,
            fallback_passwords=settings.lr_fallback_password_list,
            # La fiche cible une MAC ; la session part sur une IP qui a pu être
            # redonnée à un autre abonné.
            expected_mac=lr.mac_address,
            force=force,
        )

    if ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
        lr.ssh_host_fingerprint = observed_fp
    if used_pw and used_pw != primary_pw:
        logger.info(
            "uisp_enroll: LR '%s' (%s) — mot de passe de repli accepté, promu sur la fiche.",
            lr.name, lr.ip_address,
        )
        lr.ssh_password = used_pw

    if ok:
        # ⚠️ Une ABSTENTION ne date PAS `uisp_enrolled_at` : elle signifie « on
        # n'a rien fait », pas « enrôlé maintenant ». Confondre les deux
        # afficherait « ✓ clé posée » sur un équipement qu'on n'a pas touché —
        # et qui porte peut-être une clé orpheline, donc précisément celui qu'il
        # faut continuer à voir comme à traiter.
        if ssh_service.is_already_provisioned(msg):
            await session.commit()  # épinglage / promotion de mot de passe seuls
            logger.info(
                "uisp_enroll: LR '%s' (id=%d) déjà provisionné — non modifié : %s",
                lr.name, lr.id, msg,
            )
            return True, f"{lr.name} : {msg}"

        lr.uisp_enrolled_at = _now()
        await session.commit()
        logger.warning(
            "UISP ENROLL — LR '%s' (id=%d, %s, %s) enrôlé : %s",
            lr.name, lr.id, lr.ip_address, lr.mac_address, msg,
        )
        return True, f"{lr.name} : {msg}"

    await session.commit()  # persiste l'épinglage / la promotion de mot de passe
    logger.warning(
        "uisp_enroll: LR '%s' (id=%d, %s) NON enrôlé : %s",
        lr.name, lr.id, lr.ip_address, msg,
    )
    return False, f"{lr.name} : {msg}"


async def enroll_many(
    session: AsyncSession, lr_ids: list[int] | None = None, force: bool = False
) -> dict[str, Any]:
    """Enrôle en lot les LR vus par le radio mais absents de UISP.

    ``lr_ids`` restreint l'opération à une sélection ; omis, la cible est la
    population entière de l'anomalie (`last_discovered_at` renseigné et
    `uisp_synced_at` NULL) — la même requête que
    `access_diagnostics_service.get_radio_only_not_in_uisp`, pour que le bouton
    de la page agisse exactement sur ce qu'elle affiche.

    Séquentiel à dessein : `enroll_lr` attend l'adoption de chaque équipement
    (jusqu'à 45 s), et un `gather` sur des centaines de LR ouvrirait autant de
    sessions que le sémaphore en laisse passer *tout en* gardant les autres en
    attente — sans gain, puisque le facteur limitant est la radio. Chaque LR est
    commité au fur et à mesure : une interruption ne perd pas ce qui a marché.
    """
    if not enrollment_available():
        return {
            "attempted": 0, "enrolled": 0, "skipped": 0, "failed": 0,
            "results": [],
            "message": (
                "Aucune clé UISP configurée (UISP_DEVICE_KEY) — enrôlement "
                "indisponible."
            ),
        }

    query = select(Lr)
    if lr_ids:
        query = query.where(Lr.id.in_(lr_ids))
    else:
        query = query.where(
            Lr.last_discovered_at.is_not(None),
            Lr.uisp_synced_at.is_(None),
        )
    targets = list((await session.execute(query)).scalars().all())

    results: list[dict[str, Any]] = []
    enrolled = 0
    skipped = 0
    for lr in targets:
        ok, msg = await enroll_lr(session, lr, force=force)
        was_skip = ok and ssh_service.is_already_provisioned(msg)
        skipped += int(was_skip)
        enrolled += int(ok and not was_skip)
        results.append({
            "id": lr.id, "name": lr.name, "mac": lr.mac_address,
            "ok": ok, "skipped": was_skip, "message": msg,
        })

    failed = len(targets) - enrolled - skipped
    logger.warning(
        "UISP ENROLL (lot) — %d tentés, %d enrôlés, %d déjà provisionnés, %d en échec",
        len(targets), enrolled, skipped, failed,
    )
    # Le compte des SAUTÉS est dit explicitement : sans lui, un lot où tout le
    # monde était déjà provisionné (clés orphelines) ressemblerait à un franc
    # succès alors que rien n'a été régularisé.
    detail = f" {skipped} déjà provisionné(s), non modifié(s)." if skipped else ""
    return {
        "attempted": len(targets),
        "enrolled": enrolled,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "message": f"{enrolled}/{len(targets)} équipement(s) enrôlé(s) dans UISP.{detail}",
    }
