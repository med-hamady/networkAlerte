"""
UISP Power device service.

Polls the local REST API of a UISP Power device to retrieve:
  - Voltage, current, power consumption per outlet
  - Battery voltage and percentage (if present)
  - Overall device status

API format (Ubiquiti mFi / UISP Power):
  POST http://<ip>/api/v1.0/login/   → session cookie
  GET  http://<ip>/api/v1.0/sensors/ → sensor readings

Two response formats are handled:
  Format A: {"sensors": [{"type": "voltage", "value": 24.1}, ...]}
  Format B: {"outputs": [{"voltage": 24.0, "current": 0.5, "power": 12.0}], "battery": {...}}
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class UISPPowerClient:
    """Stateless HTTP client for the UISP Power local REST API."""

    def __init__(self, host: str, username: str, password: str, port: int = 80):
        self._base = f"http://{host}:{port}"
        self._username = username
        self._password = password

    async def _login(self, client: httpx.AsyncClient) -> dict:
        """Authenticate and return session cookies."""
        resp = await client.post(
            f"{self._base}/api/v1.0/login/",
            json={"username": self._username, "password": self._password},
            timeout=5,
        )
        resp.raise_for_status()
        return dict(resp.cookies)

    async def get_sensors(self) -> dict | None:
        """
        Fetch raw sensor data from the device.
        Returns the parsed JSON dict, or None if the device is unreachable or auth fails.
        """
        try:
            async with httpx.AsyncClient(timeout=10, verify=get_settings().tls_verify_devices) as client:
                cookies = await self._login(client)
                resp = await client.get(
                    f"{self._base}/api/v1.0/sensors/",
                    cookies=cookies,
                    timeout=5,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "UISP Power HTTP error (%s): %s", self._base, exc.response.status_code
            )
        except httpx.RequestError as exc:
            logger.debug("UISP Power unreachable (%s): %s", self._base, exc)
        except Exception as exc:
            logger.error("UISP Power unexpected error (%s): %s", self._base, exc)
        return None


def parse_power_readings(raw: dict) -> dict[str, float | None]:
    """
    Parse raw API response into a normalized dict of power metrics.
    All values are floats or None if unavailable.
    """
    result: dict[str, float | None] = {
        "voltage": None,
        "current": None,
        "power": None,
        "battery_voltage": None,
        "battery_percentage": None,
    }

    # Format A: sensors list
    for sensor in raw.get("sensors", []):
        stype = sensor.get("type", "")
        value = sensor.get("value")
        if value is None:
            continue
        if stype == "voltage":
            result["voltage"] = float(value)
        elif stype == "current":
            result["current"] = float(value)
        elif stype == "power":
            result["power"] = float(value)

    # Format B: outputs list (first outlet used as reference)
    outputs = raw.get("outputs", [])
    if outputs:
        out = outputs[0]
        result["voltage"] = float(out["voltage"]) if "voltage" in out else result["voltage"]
        result["current"] = float(out["current"]) if "current" in out else result["current"]
        result["power"] = float(out["power"]) if "power" in out else result["power"]

    # Battery (present on devices with UPS/battery backup)
    battery = raw.get("battery", {})
    if "voltage" in battery:
        result["battery_voltage"] = float(battery["voltage"])
    if "percentage" in battery:
        result["battery_percentage"] = float(battery["percentage"])

    return result


async def poll_uisp_power(
    host: str,
    username: str = "ubnt",
    password: str = "ubnt",
    port: int = 80,
) -> dict[str, float | None] | None:
    """
    Poll a UISP Power device and return normalized power metrics.
    Returns None if the device is unreachable or authentication fails.
    """
    client = UISPPowerClient(host, username, password, port)
    raw = await client.get_sensors()
    if raw is None:
        return None
    return parse_power_readings(raw)
