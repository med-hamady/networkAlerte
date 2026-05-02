"""
WhatChimp client — sends incident alerts via WhatsApp Business webhook.

WhatChimp's "Webhook Workflow" receives a POST from this module and sends a
WhatsApp template message to the phone number(s) configured on the WhatChimp
side. Phone routing is handled by WhatChimp, not by this module.

Channel config expected in notification_channels.config:
  {
    "webhook_url": "https://app.whatchim.com/api/workflows/webhook/XXXX",
    "secret_token": "<optional bearer token>"
  }

Environment variables:
  WHATSAPP_TEST_MODE=true  — log payload without sending (returns True)
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_MAX_RETRIES = 1  # one retry on HTTP 5xx before giving up


async def send_whatsapp_alert(
    webhook_url: str,
    payload: dict,
    *,
    test_mode: bool = False,
    secret_token: str | None = None,
) -> bool:
    """POST `payload` to a WhatChimp webhook URL. Returns True on success.

    When `test_mode` is True the payload is logged but no HTTP request is made
    (always returns True). `secret_token`, when provided, is sent as an
    ``Authorization: Bearer`` header. Retries once on HTTP 5xx.
    """
    if test_mode:
        logger.info("[WHATSAPP TEST] payload=%s", payload)
        return True

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret_token:
        headers["Authorization"] = f"Bearer {secret_token}"

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(webhook_url, json=payload, headers=headers)

            if resp.status_code < 300:
                logger.debug("WhatChimp delivered (HTTP %d)", resp.status_code)
                return True

            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                logger.warning(
                    "WhatChimp HTTP %d — retrying (%d/%d)",
                    resp.status_code, attempt + 1, _MAX_RETRIES + 1,
                )
                continue

            logger.error("WhatChimp HTTP %d — %s", resp.status_code, webhook_url)
            return False

        except httpx.TimeoutException:
            if attempt < _MAX_RETRIES:
                logger.warning("WhatChimp timeout — retrying (attempt %d)", attempt + 1)
                continue
            logger.error(
                "WhatChimp timeout after %d attempt(s) — %s",
                _MAX_RETRIES + 1, webhook_url,
            )
            return False

        except Exception as exc:
            logger.error("WhatChimp delivery failed: %s", exc)
            return False

    return False
