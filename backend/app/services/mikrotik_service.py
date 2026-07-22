"""Client MikroTik (RouterOS) — blocage de secours par règle firewall.

Rôle dans le système
--------------------
Le blocage nominal se fait sur le LR du client (SSH, cf. ``client_block_service``).
Il échoue quand le LR est éteint, refuse le SSH ou rejette nos identifiants — et
un client qu'on n'arrive pas à couper garde son accès malgré son impayé (run du
2026-07-14 : 163 clients sur 222 dans ce cas).

Le routeur de cœur, lui, coupe depuis le centre du réseau : il n'a besoin ni du
LR, ni du SSH, ni que le client soit joignable. Ce module est donc le **filet de
sécurité** de ``client_block_service``, jamais le mécanisme principal.

Ce que fait une règle
---------------------
Réplique exacte de ce que pose le système historique (``add_rules.php``), pour
que les deux mécanismes soient interchangeables pendant la bascule::

    /ip/firewall/filter/add chain=forward src-mac-address=<MAC MAJUSCULES>
        action=drop comment=<...> place-before=0

``place-before=0`` insère en tête de la chaîne ``forward`` pour passer avant
d'éventuelles règles d'autorisation.

Le retrait cible **toutes** les règles ``drop`` portant cette MAC — y compris
celles posées par le système historique. C'est voulu : chaque déblocage nettoie
le legacy au passage.

Contrat d'erreur
----------------
Ces fonctions **ne lèvent jamais** (même contrat que ``whatsapp_service``) : un
routeur injoignable ne doit pas faire échouer l'action métier ni interrompre un
lot. Elles retournent ``(ok, message)`` et l'appelant décide — en pratique
``client_block_service`` laisse l'état en désaccord et le job de renforcement
retentera au cycle suivant.

⚠️ Limite connue : la règle porte sur la MAC **du LR**. Elle ne matche donc que
si le trafic du client arrive au routeur avec cette MAC en source — vrai quand le
LR route (mode routeur), faux en mode bridge où le LR est transparent en L2 et
laisse passer la MAC de l'équipement du client. Les LR en bridge sont de toute
façon refusés en amont (409), cf. ``fai.fai_block``.
"""

import asyncio
import contextlib
import datetime
import logging
import unicodedata

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Plafond de sessions API RouterOS simultanées. Chaque appel ouvre une connexion
# TCP + un login : un lot de 200 déblocages (le batch du matin) en ouvrirait 200
# d'un coup, ce que le routeur refuserait. Les appels au-delà attendent leur tour.
_API_CONCURRENCY = asyncio.Semaphore(5)


def _normalize_mac(mac: str) -> str:
    """MAC en MAJUSCULES avec deux-points — la forme que RouterOS compare."""
    return (mac or "").strip().upper()


def _sanitize_comment(comment: str) -> str:
    """Commentaire réduit à de l'ASCII imprimable, tronqué.

    Les noms clients portent des accents (« aicha Ely zeine ») et parfois des
    marques de direction Unicode invisibles ; RouterOS les accepte mal et la
    règle partirait tronquée ou refusée.
    """
    folded = unicodedata.normalize("NFKD", comment or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    cleaned = " ".join(ascii_only.split())
    return cleaned[:120]


def build_comment(label: str) -> str:
    """Commentaire de règle horodaté, préfixé pour identifier NOS règles.

    Le préfixe distingue les règles posées par le superviseur de celles du
    système historique quand on lit la liste sur le routeur.
    """
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    return _sanitize_comment(f"supervisor {label} {ts}")


def is_enabled() -> bool:
    """Le repli routeur est-il activé (et configuré) ?

    Sans mot de passe, on considère le repli désactivé plutôt que d'échouer à
    chaque appel : un déploiement qui oublie `MIKROTIK_PASSWORD` doit se comporter
    comme avant la fonctionnalité, pas casser tous les blocages.
    """
    settings = get_settings()
    return bool(settings.mikrotik_enabled and settings.mikrotik_password)


@contextlib.contextmanager
def _session():
    """Session API RouterOS, toujours refermée (synchrone — dans un thread).

    La fermeture est best-effort : une session déjà tombée ne doit pas masquer le
    résultat de l'opération qu'on vient de faire.
    """
    # Import paresseux : librouteros n'est nécessaire que si le repli est activé,
    # et son absence ne doit pas empêcher le backend de démarrer.
    from librouteros import connect as ros_connect

    settings = get_settings()
    api = ros_connect(
        username=settings.mikrotik_user,
        password=settings.mikrotik_password,
        host=settings.mikrotik_host,
        port=settings.mikrotik_port,
        timeout=settings.mikrotik_timeout,
    )
    try:
        yield api
    finally:
        with contextlib.suppress(Exception):
            api.close()


def _find_drop_rule_ids(api, mac: str) -> list[str]:
    """`.id` des règles drop ciblant cette MAC (liste vide si aucune)."""
    rules = list(api.rawCmd(
        "/ip/firewall/filter/print",
        f"?src-mac-address={mac}",
        "?action=drop",
    ))
    return [r.get(".id") for r in rules if r.get(".id")]


def _block_sync(mac: str, comment: str) -> tuple[bool, str]:
    with _session() as api:
        if _find_drop_rule_ids(api, mac):
            return True, "Règle de blocage déjà présente sur le routeur."
        tuple(api.rawCmd(
            "/ip/firewall/filter/add",
            "=chain=forward",
            f"=src-mac-address={mac}",
            "=action=drop",
            f"=comment={comment}",
            "=place-before=0",
        ))
        return True, "Règle de blocage posée sur le routeur."


def _unblock_sync(mac: str) -> tuple[bool, str]:
    with _session() as api:
        rule_ids = _find_drop_rule_ids(api, mac)
        if not rule_ids:
            return True, "Aucune règle de blocage sur le routeur."
        removed, failed = 0, 0
        for rule_id in rule_ids:
            try:
                tuple(api.rawCmd("/ip/firewall/filter/remove", f"=.id={rule_id}"))
                removed += 1
            except Exception as exc:  # noqa: BLE001 — une règle récalcitrante ne bloque pas les autres
                failed += 1
                logger.warning("mikrotik: remove .id=%s a échoué : %s", rule_id, exc)
        if failed:
            return False, f"{removed} règle(s) retirée(s), {failed} en échec."
        return True, f"{removed} règle(s) de blocage retirée(s) du routeur."


def _is_blocked_sync(mac: str) -> bool:
    with _session() as api:
        return bool(_find_drop_rule_ids(api, mac))


async def _run(op: str, fn, *args) -> tuple[bool, str]:
    """Exécute une opération RouterOS dans un thread, sans jamais lever."""
    if not is_enabled():
        return False, "Repli routeur désactivé (MIKROTIK_ENABLED / mot de passe)."
    try:
        async with _API_CONCURRENCY:
            return await asyncio.to_thread(fn, *args)
    except ImportError:
        logger.error(
            "mikrotik: librouteros n'est pas installé — le repli routeur ne peut "
            "pas fonctionner (ajouter la dépendance et reconstruire l'image)."
        )
        return False, "librouteros absent de l'image backend."
    except Exception as exc:  # noqa: BLE001 — le routeur ne doit pas casser l'action métier
        logger.warning("mikrotik: %s a échoué : %s: %s", op, type(exc).__name__, exc)
        return False, f"Routeur injoignable ou refus ({type(exc).__name__}: {exc})"[:200]


async def block_by_mac(mac: str, comment: str = "") -> tuple[bool, str]:
    """Pose une règle drop pour cette MAC. Idempotent (pas de doublon)."""
    if not mac:
        return False, "MAC absente — impossible de bloquer sur le routeur."
    return await _run("block", _block_sync, _normalize_mac(mac), comment)


async def unblock_by_mac(mac: str) -> tuple[bool, str]:
    """Retire toutes les règles drop de cette MAC. Idempotent (0 règle = succès)."""
    if not mac:
        return False, "MAC absente — impossible de débloquer sur le routeur."
    return await _run("unblock", _unblock_sync, _normalize_mac(mac))


async def is_blocked_by_mac(mac: str) -> bool | None:
    """True/False si le routeur répond, None si on n'a pas pu lui demander."""
    if not mac or not is_enabled():
        return None
    try:
        async with _API_CONCURRENCY:
            return await asyncio.to_thread(_is_blocked_sync, _normalize_mac(mac))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mikrotik: lecture d'état a échoué : %s", exc)
        return None
