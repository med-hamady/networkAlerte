"""Journal des actions de blocage / déblocage — fichier texte dédié.

Pourquoi un fichier et pas les logs Docker : ces lignes sont une **piste d'audit
métier** (qui a été coupé, quand, sur ordre de qui, avec quel résultat), pas du
debug applicatif. Elles doivent survivre à un `docker compose down`, se lire sans
outil, et pouvoir être relues par un non-développeur — typiquement pour répondre à
« pourquoi ce client était coupé le 14 ? ».

Le fichier vit dans un volume bind-monté (`FAI_LOG_PATH`, défaut
``/app/logs/fai_actions.log`` → ``backend/logs/`` côté hôte). Une ligne par action,
horodatée UTC :

    2026-07-14T11:02:47Z | BLOCK   | ok=False | d0:21:f9:f6:07:c2 | 36086261-Toutou |
    mode=full | source=payment | Blocage enregistré mais NON appliqué (timed out)

L'écriture ne doit JAMAIS faire échouer l'action métier : un disque plein ou un
volume mal monté ne peut pas empêcher de couper un client. Toute erreur d'écriture
est avalée (loggée en WARNING) — d'où le try/except large.
"""

import collections
import datetime
import logging
import os
import re

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Plafond de lignes remontées en mémoire pour l'affichage. Le journal est un
# fichier qui grossit sans fin ; la page ne montre que l'historique récent, donc
# on ne lit que la queue du fichier (deque à taille bornée = O(1) mémoire).
_MAX_SCAN_LINES = 5000

# ── Preuves d'exécution ──────────────────────────────────────────────────────
# Une entrée du journal est UNE ligne (le message y est aplati). La preuve, elle,
# est la transcription multi-lignes d'une session SSH : elle ne peut donc pas
# tenir dans la ligne et vit dans un fichier à côté.
#
# ⚠️ Le nom du fichier est DÉDUIT de (timestamp, MAC, action) — les trois champs
# que la ligne porte déjà — plutôt que référencé par une colonne supplémentaire.
# Raison : ajouter un champ changerait le format sur disque, et `_parse` (qui
# fait un split à arité fixe) rejetterait alors toutes les lignes déjà écrites,
# c.-à-d. l'historique d'audit entier. Ici, une ligne sans preuve est simplement
# une ligne dont le fichier n'existe pas — l'ancien historique reste lisible.
#
# Corollaire : deux actions de MÊME type sur la MÊME MAC dans la MÊME seconde
# se recouvriraient. En pratique impossible — une session SSH de blocage dure
# plusieurs secondes, le sémaphore les sérialise et l'enforcement est à 120 s.
_EVIDENCE_MAX_BYTES = 256 * 1024
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9:_-]")


def _evidence_filename(timestamp: str, mac: str | None, action: str) -> str:
    """Nom de fichier de preuve pour une entrée — déterministe et sûr.

    ⚠️ Les trois arguments arrivent de la query string côté lecture : tout
    caractère hors de l'allowlist est remplacé, et le résultat repasse par
    ``os.path.basename``. Sans ça, un `mac=../../etc/passwd` ferait servir un
    fichier arbitraire du conteneur.
    """
    parts = [
        _SAFE_TOKEN_RE.sub("_", (timestamp or "").strip()),
        _SAFE_TOKEN_RE.sub("_", (mac or "nomac").strip().lower()),
        _SAFE_TOKEN_RE.sub("_", (action or "").strip().upper()),
    ]
    return os.path.basename("_".join(parts).replace(":", "") + ".txt")


def _evidence_path(timestamp: str, mac: str | None, action: str) -> str:
    return os.path.join(
        get_settings().fai_evidence_dir, _evidence_filename(timestamp, mac, action)
    )


def has_evidence(timestamp: str, mac: str | None, action: str) -> bool:
    """Une preuve est-elle archivée pour cette entrée ?"""
    try:
        return os.path.isfile(_evidence_path(timestamp, mac, action))
    except Exception:  # noqa: BLE001
        return False


def read_evidence(timestamp: str, mac: str | None, action: str) -> str | None:
    """Transcription archivée pour cette entrée, ou ``None`` si absente."""
    path = _evidence_path(timestamp, mac, action)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(_EVIDENCE_MAX_BYTES)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("fai_audit: lecture de la preuve impossible (%s) : %s", path, exc)
        return None


def _write_evidence(timestamp: str, mac: str | None, action: str, text: str) -> None:
    """Archive la transcription — best-effort, ne lève jamais.

    Même règle que le journal : une preuve qu'on n'arrive pas à écrire ne doit
    pas empêcher de couper un client.
    """
    path = _evidence_path(timestamp, mac, action)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[:_EVIDENCE_MAX_BYTES])
    except Exception as exc:  # noqa: BLE001 — l'audit ne doit pas casser l'action
        logger.warning("fai_audit: écriture de la preuve impossible (%s) : %s", path, exc)


def _line(
    action: str,
    *,
    ts: str,
    ok: bool,
    mac: str | None,
    name: str,
    mode: str | None,
    source: str,
    message: str,
) -> str:
    flat = " ".join((message or "").split())  # jamais de retour à la ligne dans une entrée
    # `source` est dérivé du motif envoyé par l'appelant : un « | » y décalerait
    # toutes les colonnes à la relecture (le message, lui, est le dernier champ et
    # peut en contenir sans risque).
    src = " ".join((source or "-").split()).replace("|", "/")
    return (
        f"{ts} | {action:<9} | ok={str(ok):<5} | {mac or '-':<17} | {name} "
        f"| mode={mode or '-'} | source={src} | {flat}\n"
    )


def log_action(
    action: str,
    *,
    ok: bool,
    mac: str | None,
    name: str,
    mode: str | None = None,
    source: str = "payment",
    message: str = "",
    evidence: str | None = None,
) -> None:
    """Ajoute une ligne au journal des actions FAI (best-effort, ne lève jamais).

    ``action`` : BLOCK | UNBLOCK | RETRY_OK | ABANDON | IDENT_KO (l'équipement
    joint n'était pas celui de la fiche → rien n'a été tenté). ``source`` : qui a demandé
    (``payment`` = API du système de paiement, ``enforce`` = job de renforcement).

    ``evidence`` : transcription de la session SSH (ce qui a été envoyé à
    l'équipement et ce qu'il a répondu). Archivée dans un fichier à part, indexé
    par le MÊME horodatage que la ligne — d'où le calcul du ``ts`` ici plutôt que
    dans :func:`_line` : les deux doivent porter la même seconde, sinon la ligne
    ne retrouve plus sa preuve.
    """
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = get_settings().fai_log_path
    # La preuve d'abord : une ligne de journal qui promet une preuve absente est
    # pire que pas de preuve du tout. Dans l'autre ordre, un échec d'écriture du
    # journal laisse au pire un fichier orphelin, que personne ne va chercher.
    if evidence:
        _write_evidence(ts, mac, action, evidence)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # O_APPEND : les écritures concurrentes (API + job) ne s'entremêlent pas.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_line(
                action, ts=ts, ok=ok, mac=mac, name=name, mode=mode,
                source=source, message=message,
            ))
    except Exception as exc:  # noqa: BLE001 — l'audit ne doit pas casser l'action
        logger.warning("fai_audit: écriture du journal impossible (%s) : %s", path, exc)


def _parse(line: str) -> dict | None:
    """Une ligne du journal → dict, ou None si la ligne est illisible.

    Le séparateur est ` | ` et le **message est le dernier champ** : il peut donc
    contenir n'importe quoi (y compris un `|`), d'où le maxsplit — sans lui, un
    message contenant un pipe décalerait toutes les colonnes.
    """
    parts = line.rstrip("\n").split(" | ", 7)
    if len(parts) < 8:
        return None
    ts, action, ok, mac, name, mode, source, message = parts
    return {
        "timestamp": ts.strip(),
        "action": action.strip(),
        "ok": ok.strip().removeprefix("ok=").strip() == "True",
        "mac": None if mac.strip() == "-" else mac.strip(),
        "name": name.strip(),
        "mode": mode.strip().removeprefix("mode="),
        "source": source.strip().removeprefix("source="),
        "message": message.strip(),
        # Renseigné plus tard, sur les seules entrées RENDUES (cf. read_entries) :
        # un stat() par ligne lue coûterait 5000 accès disque par affichage.
        "has_evidence": False,
    }


def _is_stale_failure(
    entry: dict,
    resolved_block_macs: set[str],
    resolved_unblock_macs: set[str],
) -> bool:
    """Une ligne « non appliqué » a-t-elle été RATTRAPÉE depuis ?

    Le journal est un historique append-only : une ligne « non appliqué » y reste
    même après que l'ordre a fini par passer. Pour que l'onglet « Non appliqué »
    reflète l'état RÉEL, on cache une ligne d'échec dont l'ordre est aujourd'hui
    satisfait en base :
      - un BLOCK/RETRY_OK raté est rattrapé si la MAC est désormais coupée (SSH ou
        routeur) → `resolved_block_macs` ;
      - un UNBLOCK raté est rattrapé si la MAC n'est plus bloquée → `resolved_unblock_macs`.
    """
    if entry["ok"]:
        return False
    mac = (entry["mac"] or "").lower()
    if entry["action"] in ("BLOCK", "RETRY_OK"):
        return mac in resolved_block_macs
    if entry["action"] == "UNBLOCK":
        return mac in resolved_unblock_macs
    return False


def read_entries(
    *,
    limit: int = 200,
    action: str | None = None,
    status: str | None = None,
    search: str | None = None,
    resolved_block_macs: set[str] | None = None,
    resolved_unblock_macs: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """Retourne (entrées les plus récentes d'abord, compteurs).

    ``status`` : ``ok`` | ``failed`` | ``abandoned``. ``search`` filtre sur la MAC
    ou le nom du client. Les compteurs portent sur la **fenêtre lue** (les
    ``_MAX_SCAN_LINES`` dernières lignes), pas sur le fichier entier — c'est ce que
    la page affiche, et compter tout le fichier obligerait à le relire en entier à
    chaque rafraîchissement.

    ``resolved_block_macs`` / ``resolved_unblock_macs`` (MAC en minuscule) : ordres
    aujourd'hui satisfaits en base. Les lignes d'échec correspondantes sont
    considérées comme rattrapées → exclues de « Non appliqué » et de son compteur.
    """
    resolved_block_macs = resolved_block_macs or set()
    resolved_unblock_macs = resolved_unblock_macs or set()
    path = get_settings().fai_log_path
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tail = collections.deque(fh, maxlen=_MAX_SCAN_LINES)
    except FileNotFoundError:
        return [], {"total": 0, "ok": 0, "failed": 0, "abandoned": 0}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fai_audit: lecture du journal impossible (%s) : %s", path, exc)
        return [], {"total": 0, "ok": 0, "failed": 0, "abandoned": 0}

    entries = [e for e in (_parse(line) for line in tail) if e]
    entries.reverse()  # plus récent en tête

    def still_failed(e: dict) -> bool:
        return (
            not e["ok"]
            and e["action"] != "ABANDON"
            and not _is_stale_failure(e, resolved_block_macs, resolved_unblock_macs)
        )

    stats = {
        "total": len(entries),
        "ok": sum(1 for e in entries if e["ok"]),
        "failed": sum(1 for e in entries if still_failed(e)),
        "abandoned": sum(1 for e in entries if e["action"] == "ABANDON"),
    }

    if action:
        entries = [e for e in entries if e["action"] == action.upper()]
    if status == "ok":
        entries = [e for e in entries if e["ok"]]
    elif status == "failed":
        # « Non appliqué » = échecs ENCORE en souffrance (rattrapés exclus).
        entries = [e for e in entries if still_failed(e)]
    elif status == "abandoned":
        entries = [e for e in entries if e["action"] == "ABANDON"]
    if search:
        needle = search.strip().lower()
        entries = [
            e for e in entries
            if needle in (e["mac"] or "").lower() or needle in e["name"].lower()
        ]

    shown = entries[:limit]
    # Marquer les entrées qui ont une preuve archivée — après filtre ET limite,
    # donc au plus `limit` stat() (200 par défaut) au lieu d'un par ligne lue.
    for e in shown:
        e["has_evidence"] = has_evidence(e["timestamp"], e["mac"], e["action"])
    return shown, stats
