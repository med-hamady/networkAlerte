"""Envoi de SMS par la passerelle HTTP (route Mattel).

`POST {SMS_GATEWAY_URL}` en Basic auth, corps form-urlencoded `Phone` /
`Text` / `SMSCRoute`, UN destinataire par requête. La passerelle répond par une
page HTML, jamais du JSON : le SEUL critère de succès est la chaîne
« Message Submitted » dans un 200 — tout le reste (autre code, erreur réseau,
délai dépassé, texte absent) est un échec.

⚠️ « Message Submitted » = la passerelle a ACCEPTÉ le SMS, pas que le téléphone
l'a reçu : la passerelle ne fournit aucun accusé de livraison.

⚠️ **Un délai dépassé n'est pas un échec certain** : la passerelle a pu envoyer
le SMS sans avoir eu le temps de répondre. Le détail le dit en clair, pour que
l'opérateur décide en connaissance de cause avant de relancer — on ne relance
jamais automatiquement.

`httpx` n'envoie pas `Expect: 100-continue` (que la passerelle refuse) : rien à
neutraliser, contrairement à curl ou PHP.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SUCCESS_MARKER = "Message Submitted"
_MESSAGE_ID_RE = re.compile(r"MessageID=([^,\s]+)")


async def send_sms(phone: str, text: str) -> tuple[bool, str]:
    """Envoie un SMS à `phone` (format `+222XXXXXXXX`). Rend `(ok, détail)`, ne lève jamais.

    Détail sur succès : le nombre de segments facturés et le premier MessageID.
    """
    settings = get_settings()
    if not settings.sms_available:
        return False, "SMS non configuré (SMS_GATEWAY_URL / _USERNAME / _PASSWORD)"
    if not text:
        return False, "message vide"

    try:
        async with httpx.AsyncClient(timeout=settings.sms_gateway_timeout_seconds) as client:
            resp = await client.post(
                settings.sms_gateway_url,
                auth=(settings.sms_gateway_username, settings.sms_gateway_password),
                data={"Phone": phone, "Text": text, "SMSCRoute": settings.sms_gateway_route},
            )
    except httpx.TimeoutException:
        return False, (
            "délai dépassé — la passerelle a PEUT-ÊTRE envoyé le SMS : "
            "vérifier avant de relancer"
        )
    except httpx.RequestError as exc:
        return False, f"réseau : {exc}"[:300]
    except Exception as exc:
        return False, f"erreur : {exc}"[:300]

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    body = resp.text
    if _SUCCESS_MARKER not in body:
        snippet = re.sub(r"<[^>]+>", " ", body)
        return False, f"refusé par la passerelle : {' '.join(snippet.split())[:200]}"

    ids = _MESSAGE_ID_RE.findall(body)
    segments = max(1, len(ids))
    first = ids[0] if ids else "?"
    return True, f"{segments} segment(s), id {first}"
