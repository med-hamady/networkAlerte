"""
UISP Power Pro device service.

Polls the local REST API of a UISP Power Pro device to retrieve:
  - Output voltage, current, and power delivered to loads (DC output)
  - Battery charge level, voltage, and temperature (Li-Ion UPS)
  - Overall device status

API protocol (UISP Power Pro firmware, served over HTTPS):
  POST https://<ip>/api/v1.0/user/login
       body: {"username": ..., "password": ...}
       -> 200 with X-Auth-Token header
  GET  https://<ip>/api/v1.0/statistics
       header: X-Auth-Token: <token>
       -> JSON [{ "device": { "outputPower": {...}, "power": [...], ... } }]

The legacy /api/v1.0/login/ + /api/v1.0/sensors/ endpoints used by older
mFi/UISP Power firmware return 401/404 on this firmware revision, so the
service targets the modern path exclusively.
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class UISPPowerClient:
    """Stateless HTTPS client for the UISP Power Pro local REST API."""

    def __init__(self, host: str, username: str, password: str, port: int = 443):
        # The firmware forces HTTPS; certs are self-signed so verification is
        # toggled via Settings.tls_verify_devices (off by default).
        self._base = f"https://{host}:{port}"
        self._username = username
        self._password = password

    async def _login(self, client: httpx.AsyncClient) -> str | None:
        """Authenticate and return the X-Auth-Token, or None on failure."""
        resp = await client.post(
            f"{self._base}/api/v1.0/user/login",
            json={"username": self._username, "password": self._password},
            timeout=5,
        )
        resp.raise_for_status()
        # Firmware returns the token in a response header (case-insensitive).
        token = resp.headers.get("x-auth-token")
        if not token:
            logger.warning("UISP Power login OK but no x-auth-token header (%s)", self._base)
        return token

    async def get_statistics(self) -> dict | None:
        """
        Fetch /api/v1.0/statistics and return the inner `device` dict, or
        None if the device is unreachable / auth fails / payload malformed.
        """
        try:
            async with httpx.AsyncClient(timeout=10, verify=get_settings().tls_verify_devices) as client:
                token = await self._login(client)
                if not token:
                    return None
                resp = await client.get(
                    f"{self._base}/api/v1.0/statistics",
                    headers={"x-auth-token": token},
                    timeout=5,
                )
                resp.raise_for_status()
                payload = resp.json()
                # API returns an array with a single sample
                if isinstance(payload, list) and payload:
                    return payload[0].get("device")
                logger.warning("UISP Power statistics: unexpected payload shape (%s)", self._base)
                return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "UISP Power HTTP error (%s): %s", self._base, exc.response.status_code
            )
        except httpx.RequestError as exc:
            logger.debug("UISP Power unreachable (%s): %s", self._base, exc)
        except Exception as exc:
            logger.error("UISP Power unexpected error (%s): %s", self._base, exc)
        return None


def parse_power_readings(device: dict) -> dict[str, float | None]:
    """
    Extract a normalized metrics dict from the `device` block returned by
    /api/v1.0/statistics. All values are floats, or None if absent.

    Mapping:
      voltage / current / power → outputPower.{voltage, current, power}
        (these describe the load the UISP Power is currently driving — the
        "is the device delivering" signal we want to monitor)
      battery_percentage / battery_voltage → first power[] entry whose
        battery.type == "li-ion" (the UPS battery, as opposed to lead-acid
        external packs that the device may also report)
    """
    result: dict[str, float | None] = {
        "voltage": None,
        "current": None,
        "power": None,
        "battery_voltage": None,
        "battery_percentage": None,
    }

    output = device.get("outputPower") or {}
    if "voltage" in output:
        result["voltage"] = float(output["voltage"])
    if "current" in output:
        result["current"] = float(output["current"])
    if "power" in output:
        result["power"] = float(output["power"])

    # Find the Li-Ion UPS battery entry. The device may also expose lead-acid
    # external packs — we prefer Li-Ion (internal UPS) since it's the one that
    # drives the device when AC fails. Fallback to first battery found.
    li_ion: dict | None = None
    any_batt: dict | None = None
    for entry in device.get("power") or []:
        battery = entry.get("battery") or {}
        if not battery:
            continue
        any_batt = any_batt or entry
        if battery.get("type") == "li-ion":
            li_ion = entry
            break

    chosen = li_ion or any_batt
    if chosen:
        battery = chosen.get("battery") or {}
        charge_level = battery.get("chargeLevel")
        if charge_level is not None:
            result["battery_percentage"] = float(charge_level)
        # The voltage on the same entry is the battery terminal voltage
        if chosen.get("voltage") is not None:
            result["battery_voltage"] = float(chosen["voltage"])

    return result


async def poll_uisp_power(
    host: str,
    username: str = "ubnt",
    password: str = "ubnt",
    port: int = 443,
) -> dict[str, float | None] | None:
    """
    Poll a UISP Power device and return normalized power metrics.
    Returns None if the device is unreachable or authentication fails.
    """
    client = UISPPowerClient(host, username, password, port)
    device = await client.get_statistics()
    if device is None:
        return None
    return parse_power_readings(device)
