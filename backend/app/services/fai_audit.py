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

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Plafond de lignes remontées en mémoire pour l'affichage. Le journal est un
# fichier qui grossit sans fin ; la page ne montre que l'historique récent, donc
# on ne lit que la queue du fichier (deque à taille bornée = O(1) mémoire).
_MAX_SCAN_LINES = 5000


def _line(
    action: str,
    *,
    ok: bool,
    mac: str | None,
    name: str,
    mode: str | None,
    source: str,
    message: str,
) -> str:
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
) -> None:
    """Ajoute une ligne au journal des actions FAI (best-effort, ne lève jamais).

    ``action`` : BLOCK | UNBLOCK | RETRY_OK | ABANDON. ``source`` : qui a demandé
    (``payment`` = API du système de paiement, ``enforce`` = job de renforcement).
    """
    path = get_settings().fai_log_path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # O_APPEND : les écritures concurrentes (API + job) ne s'entremêlent pas.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_line(
                action, ok=ok, mac=mac, name=name, mode=mode,
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
    }


def read_entries(
    *,
    limit: int = 200,
    action: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> tuple[list[dict], dict]:
    """Retourne (entrées les plus récentes d'abord, compteurs).

    ``status`` : ``ok`` | ``failed`` | ``abandoned``. ``search`` filtre sur la MAC
    ou le nom du client. Les compteurs portent sur la **fenêtre lue** (les
    ``_MAX_SCAN_LINES`` dernières lignes), pas sur le fichier entier — c'est ce que
    la page affiche, et compter tout le fichier obligerait à le relire en entier à
    chaque rafraîchissement.
    """
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

    stats = {
        "total": len(entries),
        "ok": sum(1 for e in entries if e["ok"]),
        "failed": sum(1 for e in entries if not e["ok"] and e["action"] != "ABANDON"),
        "abandoned": sum(1 for e in entries if e["action"] == "ABANDON"),
    }

    if action:
        entries = [e for e in entries if e["action"] == action.upper()]
    if status == "ok":
        entries = [e for e in entries if e["ok"]]
    elif status == "failed":
        entries = [e for e in entries if not e["ok"]]
    elif status == "abandoned":
        entries = [e for e in entries if e["action"] == "ABANDON"]
    if search:
        needle = search.strip().lower()
        entries = [
            e for e in entries
            if needle in (e["mac"] or "").lower() or needle in e["name"].lower()
        ]

    return entries[:limit], stats
