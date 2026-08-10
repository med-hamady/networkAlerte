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
from collections.abc import Iterable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Plafond de sessions API RouterOS simultanées. Chaque appel ouvre une connexion
# TCP + un login : un lot de 200 déblocages (le batch du matin) en ouvrirait 200
# d'un coup, ce que le routeur refuserait. Les appels au-delà attendent leur tour.
_API_CONCURRENCY = asyncio.Semaphore(5)

# Message rendu quand le routeur ne portait AUCUNE règle pour cette MAC. Constante
# plutôt que littéral : l'appelant s'en sert pour distinguer « j'ai retiré quelque
# chose » de « il n'y avait rien », et un texte recopié des deux côtés divergerait
# à la première reformulation.
NO_RULE_MESSAGE = "Aucune règle de blocage sur le routeur."


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


# Marque de NOS règles dans le commentaire. Constante partagée entre l'écriture
# (`build_comment`) et la relecture (`is_supervisor_comment`) : recopiée des deux
# côtés, elle divergerait à la première reformulation et la page de contrôle
# classerait alors toutes nos règles en « historique ».
_COMMENT_PREFIX = "supervisor"


def build_comment(label: str) -> str:
    """Commentaire de règle horodaté, préfixé pour identifier NOS règles.

    Le préfixe distingue les règles posées par le superviseur de celles du
    système historique quand on lit la liste sur le routeur.
    """
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    return _sanitize_comment(f"{_COMMENT_PREFIX} {label} {ts}")


def is_supervisor_comment(comment: str) -> bool:
    """La règle porte-t-elle notre marque ?

    Sert à séparer, sur une lecture du routeur, ce que le superviseur a posé de
    ce qui vient du système historique (``add_rules.php``). ⚠️ C'est un indice
    d'ORIGINE, pas une preuve : un commentaire s'édite à la main, et une règle
    legacy coupe le client exactement comme la nôtre.
    """
    return (comment or "").strip().lower().startswith(_COMMENT_PREFIX)


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


def _as_bool(value) -> bool:
    """RouterOS rend `true`/`false` — librouteros les caste, mais pas toujours."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_rules(raw: Iterable[dict]) -> list[dict]:
    """Règles brutes RouterOS → règles de **coupure client**, forme exploitable.

    Fonction pure (aucune E/S) : c'est elle qui décide ce qui compte comme une
    coupure client, et c'est donc elle qu'on teste.

    Le filtrage est refait ici alors que la requête l'a déjà demandé au routeur.
    Ce n'est pas de la redite défensive gratuite : la requête part sur le
    protocole API (plusieurs mots ``?`` combinés), et une lecture qui se
    tromperait afficherait des règles **sans rapport avec les clients** — du NAT,
    du pare-feu d'infrastructure — dans une page qui promet des blocages
    d'abonnés. Un contrôle en Python coûte zéro et rend l'affirmation vraie
    quoi qu'il arrive.

    Une règle sans ``src-mac-address`` est écartée : elle ne cible pas un abonné.
    """
    rules: list[dict] = []
    for raw_rule in raw:
        if raw_rule.get("chain") != "forward" or raw_rule.get("action") != "drop":
            continue
        mac = (raw_rule.get("src-mac-address") or "").strip()
        if not mac:
            continue
        comment = (raw_rule.get("comment") or "").strip()
        rules.append({
            "id": raw_rule.get(".id"),
            "mac": mac.upper(),
            "comment": comment,
            # Une règle DÉSACTIVÉE ne coupe personne. Elle reste listée (elle
            # existe sur le routeur, et sa présence explique qu'un client
            # « bloqué » soit en ligne) mais l'appelant doit pouvoir la
            # distinguer d'une coupure effective.
            "disabled": _as_bool(raw_rule.get("disabled")),
            # Posée par un protocole (pas à la main ni par nous) : on ne la
            # retirerait pas de la même façon.
            "dynamic": _as_bool(raw_rule.get("dynamic")),
            "packets": _as_int(raw_rule.get("packets")),
            "bytes": _as_int(raw_rule.get("bytes")),
        })
    return rules


def _list_sync() -> list[dict]:
    with _session() as api:
        raw = list(api.rawCmd(
            "/ip/firewall/filter/print",
            "?chain=forward",
            "?action=drop",
        ))
    return parse_rules(raw)


async def list_client_block_rules() -> tuple[list[dict], str | None]:
    """Toutes les règles de coupure client posées sur le routeur.

    Retourne ``(règles, erreur)`` — et **ne lève jamais** (contrat du module) :
    une consultation ne doit pas casser la page qui l'affiche. ``erreur``
    renseignée ⇒ la liste est vide et ne prouve **rien** (« aucune règle » et
    « je n'ai pas pu demander » ne se confondent pas).

    ⚠️ Portée : ``chain=forward`` uniquement, c.-à-d. le blocage d'abonnés tel
    que nous et le système historique le posons. Une règle drop sur une autre
    chaîne n'apparaît pas ici — le retrait (``unblock_by_mac``), lui, reste
    volontairement plus large et nettoie la MAC quelle que soit sa chaîne.
    """
    if not is_enabled():
        return [], "Repli routeur désactivé (MIKROTIK_ENABLED / mot de passe)."
    try:
        async with _API_CONCURRENCY:
            return await asyncio.to_thread(_list_sync), None
    except ImportError:
        logger.error("mikrotik: librouteros n'est pas installé — lecture impossible.")
        return [], "librouteros absent de l'image backend."
    except Exception as exc:  # noqa: BLE001 — une lecture ne doit pas lever
        logger.warning("mikrotik: list a échoué : %s: %s", type(exc).__name__, exc)
        return [], f"Routeur injoignable ou refus ({type(exc).__name__}: {exc})"[:200]


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
            return True, NO_RULE_MESSAGE
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
