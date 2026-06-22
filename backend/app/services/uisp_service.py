"""
UISP / UNMS controller REST client.

Authenticates to the UISP controller and fetches the device inventory. Consumed
by `uisp_sync_service` to import infrastructure devices automatically, so the
operator doesn't enter each base-station Rocket / switch / UISP Power / AF60
backhaul by hand. This client is READ-ONLY against the controller.

API (UISP/UNMS, served over HTTPS):
  POST /nms/api/v2.1/user/login {username,password}
       -> 200, token returned in the `x-auth-token` response header
  GET  /nms/api/v2.1/devices    header x-auth-token
       -> [ { identification:{...}, ipAddress, mac, ... }, ... ]

Each device already embeds its site (`identification.site.name`), so the sync
does not need a separate /sites call. A static API token (UISP → Settings →
Users → API tokens) can replace the login: it is sent as `x-auth-token`
directly.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


class UISPAuthError(RuntimeError):
    """Raised when the controller rejects the credentials / token."""


class UISPClient:
    """Stateless HTTPS client for the UISP controller REST API."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        password: str = "",
        api_token: str = "",
        verify_tls: bool = False,
        timeout: int = 30,
    ):
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._api_token = api_token
        self._verify = verify_tls
        self._timeout = timeout

    async def _auth_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Return the x-auth-token header, logging in if no static token is set."""
        if self._api_token:
            return {"x-auth-token": self._api_token}
        resp = await client.post(
            f"{self._base}/nms/api/v2.1/user/login",
            json={"username": self._username, "password": self._password},
            timeout=self._timeout,
        )
        if resp.status_code in (401, 403):
            raise UISPAuthError(
                f"UISP login rejected ({resp.status_code}) — check UISP_USERNAME/PASSWORD",
            )
        resp.raise_for_status()
        token = resp.headers.get("x-auth-token")
        if not token:
            raise UISPAuthError("UISP login succeeded but no x-auth-token header returned")
        return {"x-auth-token": token}

    async def fetch_devices(self, role: str | None = None) -> list[dict]:
        """Return the controller's device list (raw UISP dicts).

        `role` (e.g. "station" or "ap") narrows the query via the controller's
        `?role=` filter — the infra sync calls without it (all devices), the
        station sync passes role="station". Raises UISPAuthError on bad
        credentials, httpx errors on transport/HTTP failures — the caller (sync
        job) logs and skips the cycle.
        """
        async with httpx.AsyncClient(verify=self._verify, timeout=self._timeout) as client:
            headers = await self._auth_headers(client)
            resp = await client.get(
                f"{self._base}/nms/api/v2.1/devices",
                headers=headers,
                params={"role": role} if role else None,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list):
                logger.warning("UISP /devices returned a non-list payload (%s)", type(payload))
                return []
            return payload
