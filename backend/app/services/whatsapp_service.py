"""
WhatsApp transport — send alert messages to a WhatsApp group via Ultramsg.

Ultramsg (https://ultramsg.com) exposes a simple REST API per instance:

    POST {base}/{instance}/messages/chat
    form fields: token, to, body

`to` is the destination — here always the configured group chat id
(e.g. "1203630xxxxxxx@g.us"). The call is form-encoded and returns a small
JSON payload ({"sent": "true", "message": "ok", ...}) on success.

This module mirrors the defensive HTTP pattern used by the device API services
(uisp_service, ltu_api_service): a bounded httpx async client, and never raising
to the caller in a polling/notification context — failures are logged and turned
into a False return so a dead channel can't crash a job.
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 8.0
# Document uploads (PDF) are larger than a chat line and Ultramsg has to ingest
# the base64 payload, so give them a longer ceiling than a plain text send.
_DOCUMENT_TIMEOUT_S = 30.0


async def send_whatsapp(text: str) -> bool:
    """Send a text message to the configured WhatsApp group.

    Returns True only when Ultramsg accepted the message. Returns False (never
    raises) when WhatsApp is disabled/misconfigured or the API call fails — the
    caller treats this exactly like a failed email delivery.
    """
    settings = get_settings()

    if not settings.whatsapp_configured:
        logger.debug(
            "WhatsApp not configured (enabled/instance/token/group) — skipping send"
        )
        return False

    if not text:
        logger.debug("WhatsApp send skipped — empty body")
        return False

    url = f"{settings.whatsapp_base_url.rstrip('/')}/{settings.whatsapp_instance_id}/messages/chat"
    payload = {
        "token": settings.whatsapp_token,
        "to": settings.whatsapp_group_id,
        "body": text,
    }

    try:
        async with httpx.AsyncClient(timeout=_SEND_TIMEOUT_S) as client:
            resp = await client.post(url, data=payload)
    except httpx.RequestError as exc:
        logger.error("WhatsApp send failed — network error: %s", exc)
        return False
    except Exception as exc:
        logger.error("WhatsApp send unexpected error: %s", exc)
        return False

    if resp.status_code != 200:
        logger.error(
            "WhatsApp send failed — HTTP %d: %s", resp.status_code, resp.text[:200]
        )
        return False

    # Ultramsg returns {"sent": "true", ...} on success and {"error": ...} on
    # failure (still HTTP 200), so inspect the body rather than the status only.
    try:
        data = resp.json()
    except Exception:
        logger.error("WhatsApp send — non-JSON response: %s", resp.text[:200])
        return False

    sent = data.get("sent")
    if sent in (True, "true", "True") or data.get("message") == "ok":
        logger.info("WhatsApp sent to group %s", settings.whatsapp_group_id)
        return True

    logger.error("WhatsApp send rejected by Ultramsg: %s", data)
    return False


async def send_whatsapp_document(
    content: bytes, filename: str, caption: str = ""
) -> bool:
    """Send a binary document (e.g. a PDF) to the configured WhatsApp group.

    Uploads the file inline as a base64 ``document`` to Ultramsg
    ``POST {base}/{instance}/messages/document`` — no public hosting URL needed.
    Same defensive contract as :func:`send_whatsapp`: returns True only when
    Ultramsg accepted the upload, and never raises (a dead channel or a too-big
    payload must not crash the calling job).
    """
    settings = get_settings()

    if not settings.whatsapp_configured:
        logger.debug(
            "WhatsApp not configured (enabled/instance/token/group) — skipping document"
        )
        return False

    if not content:
        logger.debug("WhatsApp document send skipped — empty content")
        return False

    url = f"{settings.whatsapp_base_url.rstrip('/')}/{settings.whatsapp_instance_id}/messages/document"
    payload = {
        "token": settings.whatsapp_token,
        "to": settings.whatsapp_group_id,
        "filename": filename,
        "document": base64.b64encode(content).decode("ascii"),
        "caption": caption,
    }

    try:
        async with httpx.AsyncClient(timeout=_DOCUMENT_TIMEOUT_S) as client:
            resp = await client.post(url, data=payload)
    except httpx.RequestError as exc:
        logger.error("WhatsApp document send failed — network error: %s", exc)
        return False
    except Exception as exc:
        logger.error("WhatsApp document send unexpected error: %s", exc)
        return False

    if resp.status_code != 200:
        logger.error(
            "WhatsApp document send failed — HTTP %d: %s",
            resp.status_code, resp.text[:200],
        )
        return False

    try:
        data = resp.json()
    except Exception:
        logger.error("WhatsApp document send — non-JSON response: %s", resp.text[:200])
        return False

    sent = data.get("sent")
    if sent in (True, "true", "True") or data.get("message") == "ok":
        logger.info(
            "WhatsApp document '%s' sent to group %s", filename, settings.whatsapp_group_id
        )
        return True

    logger.error("WhatsApp document send rejected by Ultramsg: %s", data)
    return False


async def send_whatsapp_to(phone: str, text: str) -> tuple[bool, str]:
    """Envoie un message à UN numéro (et non au groupe des alertes).

    `phone` est au format international sans espace (`+22244910449`). Rend
    `(ok, détail)` : l'id Ultramsg sur succès, la raison de l'échec sinon — le
    détail est conservé tel quel pour la page « Message aux clients », où
    l'opérateur décide de relancer ou non. Ne lève jamais, comme
    `send_whatsapp`.

    ⚠️ N'exige ni `WHATSAPP_ENABLED` ni le groupe : ce sont des réglages des
    ALERTES. Couper les alertes ne doit pas couper l'écriture aux clients.
    """
    settings = get_settings()
    if not settings.whatsapp_direct_available:
        return False, "WhatsApp non configuré (WHATSAPP_INSTANCE_ID / WHATSAPP_TOKEN)"
    if not text:
        return False, "message vide"

    url = f"{settings.whatsapp_base_url.rstrip('/')}/{settings.whatsapp_instance_id}/messages/chat"
    payload = {"token": settings.whatsapp_token, "to": phone, "body": text}

    try:
        async with httpx.AsyncClient(timeout=_SEND_TIMEOUT_S) as client:
            resp = await client.post(url, data=payload)
    except httpx.RequestError as exc:
        return False, f"réseau : {exc}"[:300]
    except Exception as exc:
        return False, f"erreur : {exc}"[:300]

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code} : {resp.text[:200]}"
    try:
        data = resp.json()
    except Exception:
        return False, f"réponse non JSON : {resp.text[:200]}"

    # Ultramsg répond 200 même sur un refus : c'est le corps qui tranche.
    if data.get("sent") in (True, "true", "True") or data.get("message") == "ok":
        return True, str(data.get("id", "ok"))
    return False, str(data.get("error") or data)[:300]
