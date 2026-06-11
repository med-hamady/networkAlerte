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

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 8.0


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
