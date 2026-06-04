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


def battery_type_slug(battery_type: str | None) -> str:
    """Normalise a battery type string into a metric-name-safe slug.

    "li-ion" → "li_ion", "lead-acid" → "lead_acid". Unknown/missing → "unknown".
    Used to build per-battery metric names (battery_<slug>_pct).
    """
    if not battery_type:
        return "unknown"
    return battery_type.strip().lower().replace("-", "_").replace(" ", "_")


def parse_power_readings(device: dict) -> dict:
    """
    Extract a normalized metrics dict from the `device` block returned by
    /api/v1.0/statistics.

    Mapping:
      voltage / current / power → outputPower.{voltage, current, power}
        (these describe the load the UISP Power is currently driving — the
        "is the device delivering" signal we want to monitor)
      batteries → one entry per power[] slot that carries a battery, e.g. the
        internal Li-Ion UPS *and* an external lead-acid bank. Each entry:
        {type, type_slug, percentage, voltage, capacity_ah, connected}.
      battery_percentage / battery_voltage / battery_type → the *canonical*
        battery used for alerting: the connected battery with the LOWEST charge
        (the one closest to failing). A device with a 4.6 Ah Li-Ion UPS at 100 %
        and a 120 Ah lead-acid bank at 35 % must alert on the 35 % bank — that's
        the one that determines how long the site survives an AC outage.
        Reporting the Li-Ion 100 % (the old "prefer li-ion" rule) masked the
        real backup state.
    """
    result: dict = {
        "voltage": None,
        "current": None,
        "power": None,
        "battery_voltage": None,
        "battery_percentage": None,
        "battery_type": None,
        "batteries": [],
        "uptime_seconds": None,
        "output_max_power_w": None,
        "output_energy": None,
        "dc_outputs": [],
        "ac_connected": None,
        "state": None,
    }

    # Mains (AC) presence — True if any AC input slot is connected. None when
    # the device reports no AC slot at all (older firmware), so the caller can
    # tell "unknown" apart from "on battery". `state` is the firmware's own
    # power state label (e.g. "battery_charging_fast", "on_battery").
    result["state"] = device.get("state")
    ac_slots = [e for e in (device.get("power") or []) if e.get("psuType") == "AC"]
    if ac_slots:
        result["ac_connected"] = any(e.get("connected") for e in ac_slots)

    output = device.get("outputPower") or {}
    if "voltage" in output:
        result["voltage"] = float(output["voltage"])
    if "current" in output:
        result["current"] = float(output["current"])
    if "power" in output:
        result["power"] = float(output["power"])
    # maximalPower = rated output ceiling; powerMetter = lifetime energy counter.
    if output.get("maximalPower") is not None:
        result["output_max_power_w"] = float(output["maximalPower"])
    if output.get("powerMetter") is not None:
        result["output_energy"] = float(output["powerMetter"])

    # Device uptime (seconds).
    if device.get("uptime") is not None:
        result["uptime_seconds"] = float(device["uptime"])

    # Individual DC output ports (id, electrical readings, connection state).
    dc_outputs: list[dict] = []
    for out in output.get("dcOutput") or []:
        state = out.get("state")
        dc_outputs.append({
            "id": out.get("id"),
            "voltage": float(out["voltage"]) if out.get("voltage") is not None else None,
            "current": float(out["current"]) if out.get("current") is not None else None,
            "power": float(out["power"]) if out.get("power") is not None else None,
            "max_power_w": float(out["maximalPower"]) if out.get("maximalPower") is not None else None,
            # "disconnected" → not connected; anything else (active…) → connected.
            "connected": state is not None and state != "disconnected",
        })
    result["dc_outputs"] = dc_outputs

    # Collect every battery the device reports (Li-Ion UPS, lead-acid bank…).
    batteries: list[dict] = []
    for entry in device.get("power") or []:
        battery = entry.get("battery") or {}
        if not battery:
            continue
        charge = battery.get("chargeLevel")
        capacity = (battery.get("capacity") or {}).get("configured")
        running_time = battery.get("runningTime")
        btype = battery.get("type")
        # Power sign on the battery slot hints at charge vs discharge: the
        # firmware reports NEGATIVE power while charging and POSITIVE while the
        # battery supplies the load. BUT a full battery on mains trickles a few
        # tenths of a watt POSITIVE (noise), so a positive reading alone is not
        # "discharging". A battery is only really in use when mains is gone —
        # hence the discharging flag is gated on ac_connected being False below.
        batt_power = float(entry["power"]) if entry.get("power") is not None else None
        batteries.append({
            "type": btype,
            "type_slug": battery_type_slug(btype),
            "percentage": float(charge) if charge is not None else None,
            "voltage": float(entry["voltage"]) if entry.get("voltage") is not None else None,
            "current": float(entry["current"]) if entry.get("current") is not None else None,
            "power": batt_power,
            "capacity_ah": float(capacity) if capacity is not None else None,
            # Estimated remaining autonomy at the current load (seconds).
            "runtime_seconds": float(running_time) if running_time is not None else None,
            "connected": bool(entry.get("connected")),
            # In use = mains absent AND this slot is delivering energy (>0.1 W).
            "discharging": (
                result["ac_connected"] is False
                and batt_power is not None
                and batt_power > 0.1
            ),
        })
    result["batteries"] = batteries

    # Canonical battery for alerting = lowest-charge battery, preferring the
    # connected ones (a disconnected slot reporting 0 % must not raise a false
    # alarm). Falls back to any battery carrying a charge level.
    with_charge = [b for b in batteries if b["percentage"] is not None]
    connected = [b for b in with_charge if b["connected"]] or with_charge
    if connected:
        worst = min(connected, key=lambda b: b["percentage"])
        result["battery_percentage"] = worst["percentage"]
        result["battery_voltage"] = worst["voltage"]
        result["battery_type"] = worst["type"]

    return result


async def poll_uisp_power(
    host: str,
    username: str = "ubnt",
    password: str = "ubnt",
    port: int = 443,
) -> dict | None:
    """
    Poll a UISP Power device and return normalized power metrics.
    Returns None if the device is unreachable or authentication fails.
    """
    client = UISPPowerClient(host, username, password, port)
    device = await client.get_statistics()
    if device is None:
        return None
    return parse_power_readings(device)
