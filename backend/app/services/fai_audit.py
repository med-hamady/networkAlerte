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

import datetime
import logging
import os

from app.core.config import get_settings

logger = logging.getLogger(__name__)


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
    return (
        f"{ts} | {action:<9} | ok={str(ok):<5} | {mac or '-':<17} | {name} "
        f"| mode={mode or '-'} | source={source} | {flat}\n"
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
