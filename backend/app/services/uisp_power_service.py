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

  GET  https://<ip>/api/v1.0/system/edgepower/configuration/power
       -> {"dcOutput": [{"id": 0, "enabled": true, ...}], "battery": {...}}
  PUT  https://<ip>/api/v1.0/system/edgepower/configuration/power
       body: le MEME objet, `dcOutput[].enabled` muté -> coupure DURABLE
  POST https://<ip>/api/v1.0/system/edgepower/power-cycle
       body: {"type": "dc"}  (+ {"dc": {"id": N}} sur un Pro multi-ports)
       -> coupure TEMPORAIRE : le firmware rallume seul au bout de ~5 s

Les trois derniers chemins ne sont documentés nulle part chez Ubiquiti : ils
ont été relevés dans le bundle JS de l'interface web du boîtier (relevé du
2026-09-07 sur un UISP-P fw 1.3.0), qui est l'autorité sur ce firmware.

The legacy /api/v1.0/login/ + /api/v1.0/sensors/ endpoints used by older
mFi/UISP Power firmware return 401/404 on this firmware revision, so the
service targets the modern path exclusively.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Chemins de PILOTAGE de la sortie DC. Relevés dans le bundle JS de
# l'interface web du boîtier — Ubiquiti ne les documente nulle part, et ils ne
# répondent pas aux noms « évidents » (/outputs, /power… rendent tous un 404
# « Entity is not supported »). Ne pas les deviner : les relire dans
# `/static/scripts/bundle-*.js` si un firmware futur les déplace.
_POWER_CONFIG_PATH = "/api/v1.0/system/edgepower/configuration/power"
_POWER_CYCLE_PATH = "/api/v1.0/system/edgepower/power-cycle"

# Durée de la coupure d'un power-cycle, annoncée par l'interface web du
# boîtier (« will be turned off for 5s »). Le firmware la tient seul : on ne
# la pilote pas, on ne fait que la rapporter à l'opérateur.
_POWER_CYCLE_OFF_SECONDS = 5


class PowerControlError(Exception):
    """Échec d'une commande d'alimentation, avec un motif présentable.

    Distincte du silence de la lecture (`get_statistics` renvoie None et le
    poller passe au tour suivant) : ici l'opérateur attend devant son écran de
    savoir si le courant est coupé ou non. Un échec muet le laisserait
    supposer que c'est fait.
    """


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


    # ─────────────────────────────────────────────────────────────────
    # Pilotage de la sortie DC (écriture)
    #
    # Tout ce qui précède est de la LECTURE, appelée par le poller toutes les
    # 30 s : une panne y est bénigne (on renvoie None, le tour suivant
    # réessaie). Ce qui suit COUPE physiquement le courant d'un site, sur un
    # geste explicite d'un opérateur. D'où deux différences de contrat :
    #
    #   - on lève `PowerControlError` au lieu de renvoyer None : un échec doit
    #     être NOMMÉ à l'opérateur, jamais avalé ("rien ne s'est passé" et "je
    #     ne sais pas ce qui s'est passé" appellent des gestes différents) ;
    #   - une seule session (un seul login) porte lire-modifier-écrire-relire,
    #     pour que la vérification finale ne puisse pas tomber sur un autre
    #     état que celui qu'on vient d'écrire.
    # ─────────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
        """Ouvre UNE session authentifiée réutilisable pour plusieurs requêtes."""
        try:
            async with httpx.AsyncClient(
                timeout=15, verify=get_settings().tls_verify_devices
            ) as client:
                try:
                    token = await self._login(client)
                except httpx.HTTPStatusError as exc:
                    raise PowerControlError(
                        f"Authentification refusée par le boîtier "
                        f"(HTTP {exc.response.status_code})."
                    ) from exc
                if not token:
                    raise PowerControlError(
                        "Le boîtier a accepté le login mais n'a pas renvoyé de jeton."
                    )
                yield client, {"x-auth-token": token}
        except httpx.RequestError as exc:
            # httpx laisse str(exc) VIDE sur certains échecs de connexion — un
            # message qui s'arrête sur « injoignable : » se lit comme un bug de
            # l'application plutôt que comme un boîtier qui ne répond pas.
            reason = str(exc) or type(exc).__name__
            raise PowerControlError(f"Boîtier injoignable : {reason}") from exc

    async def _get_power_config(
        self, client: httpx.AsyncClient, headers: dict[str, str]
    ) -> dict:
        resp = await client.get(f"{self._base}{_POWER_CONFIG_PATH}", headers=headers)
        resp.raise_for_status()
        config = resp.json()
        if not isinstance(config, dict) or not isinstance(config.get("dcOutput"), list):
            raise PowerControlError(
                "Configuration d'alimentation illisible (pas de liste 'dcOutput')."
            )
        return config

    async def get_power_control_state(self) -> dict:
        """État courant de la sortie DC : activée ou non, et ce qu'elle débite.

        Sert à décider si la fiche affiche « Couper » ou « Rallumer ». La
        charge en watts vient de /statistics et non de la config : c'est elle
        qui dit ce qu'on s'apprête à éteindre.
        """
        async with self._session() as (client, headers):
            try:
                config = await self._get_power_config(client, headers)
                stats = await client.get(f"{self._base}/api/v1.0/statistics", headers=headers)
                stats.raise_for_status()
                payload = stats.json()
            except httpx.HTTPStatusError as exc:
                raise PowerControlError(
                    f"Lecture refusée par le boîtier (HTTP {exc.response.status_code})."
                ) from exc

        outputs = config["dcOutput"]
        device = payload[0].get("device") if isinstance(payload, list) and payload else {}
        readings = parse_power_readings(device or {})
        return {
            "outputs": [
                {"id": o.get("id"), "enabled": bool(o.get("enabled"))} for o in outputs
            ],
            # `enabled` = TOUT est allumé. Sur un boîtier à sortie unique les
            # deux drapeaux coïncident ; sur un Pro partiellement coupé ils
            # divergent, et la fiche doit pouvoir proposer « Rallumer » sans
            # avoir à interpréter la liste elle-même.
            "enabled": all(bool(o.get("enabled")) for o in outputs) if outputs else None,
            "any_enabled": any(bool(o.get("enabled")) for o in outputs),
            "power_w": readings.get("power"),
            "voltage_v": readings.get("voltage"),
            "current_a": readings.get("current"),
        }

    async def set_dc_outputs_enabled(self, enabled: bool) -> dict:
        """Coupure DURABLE (ou rétablissement) : `dcOutput[].enabled`.

        ⚠️ On renvoie au boîtier l'objet de configuration ENTIER tel qu'il l'a
        rendu, avec le seul `enabled` muté. Ne jamais reconstruire un corps
        minimal : le même objet porte `autoPowerOff`, le `pingWatchdog` et
        surtout `battery.capacity` (60 Ah déclarés sur le parc) — un PUT
        partiel reposerait ces réglages à leur défaut, et une capacité de
        batterie fausse dérègle l'estimation d'autonomie de tout le site.
        """
        async with self._session() as (client, headers):
            try:
                config = await self._get_power_config(client, headers)
                for output in config["dcOutput"]:
                    output["enabled"] = enabled
                applied: dict | None = None
                unanswered = False
                try:
                    resp = await client.put(
                        f"{self._base}{_POWER_CONFIG_PATH}", headers=headers, json=config
                    )
                    resp.raise_for_status()
                    # Relecture sur la MÊME session : le boîtier accuse
                    # réception du PUT avant d'avoir basculé le relais, donc un
                    # 200 ne prouve pas que le courant est coupé.
                    applied = await self._get_power_config(client, headers)
                except (httpx.TimeoutException, httpx.TransportError):
                    # Même cause que pour le power-cycle : en coupant, le
                    # boîtier se coupe du réseau. Ici la conséquence est plus
                    # lourde — on ne peut RIEN relire, donc on ne peut pas
                    # affirmer l'état obtenu. On le dit, plutôt que de deviner
                    # dans un sens ou dans l'autre.
                    unanswered = True
            except httpx.HTTPStatusError as exc:
                raise PowerControlError(
                    f"Écriture refusée par le boîtier (HTTP {exc.response.status_code})."
                ) from exc

        if unanswered or applied is None:
            return {"outputs": [], "verified": False}

        states = [bool(o.get("enabled")) for o in applied["dcOutput"]]
        if any(state is not enabled for state in states):
            raise PowerControlError(
                "Le boîtier a accepté la commande mais la sortie n'a pas changé d'état."
            )
        return {
            "verified": True,
            "outputs": [
                {"id": o.get("id"), "enabled": bool(o.get("enabled"))}
                for o in applied["dcOutput"]
            ],
        }

    async def power_cycle_dc_outputs(self) -> dict:
        """Coupure TEMPORAIRE : le firmware coupe ~5 s puis rallume seul.

        ⚠️ Le corps dépend du nombre de sorties, et ce n'est pas une
        commodité : l'interface web envoie `{"type": "dc"}` sur un boîtier à
        sortie unique et `{"type": "dc", "dc": {"id": N}}` sur un Pro, où il
        FAUT désigner le port. Envoyer la forme courte à un Pro le laisserait
        choisir à notre place ; envoyer la forme longue à un UISP-P nommerait
        un port qui n'a pas à l'être. On suit donc la règle de l'UI, calée sur
        ce que le boîtier déclare posséder.
        """
        async with self._session() as (client, headers):
            try:
                config = await self._get_power_config(client, headers)
                outputs = config["dcOutput"]
                if not outputs:
                    raise PowerControlError("Le boîtier ne déclare aucune sortie DC.")
                if len(outputs) == 1:
                    bodies: list[dict] = [{"type": "dc"}]
                else:
                    bodies = [{"type": "dc", "dc": {"id": o.get("id")}} for o in outputs]
                # Vrai = le boîtier s'est tu au lieu d'accuser réception.
                unanswered = False
                for body in bodies:
                    try:
                        resp = await client.post(
                            f"{self._base}{_POWER_CYCLE_PATH}", headers=headers, json=body
                        )
                        resp.raise_for_status()
                    except (httpx.TimeoutException, httpx.TransportError):
                        # ⚠️ LE CAS NORMAL, PAS UN ÉCHEC — vérifié sur AT1 le
                        # 2026-09-07. Le boîtier alimente le switch qui porte
                        # SON PROPRE lien de management : en coupant, il se
                        # coupe du réseau et ne peut plus répondre. Le POST
                        # meurt donc en ReadTimeout sur une commande qui a
                        # parfaitement abouti (preuve : le switch a redémarré,
                        # sysUpTime remis à zéro à la seconde près).
                        #
                        # Traiter ce silence comme une panne rendrait 502 à
                        # l'opérateur sur une coupure RÉUSSIE — il rejouerait
                        # la commande, et couperait le site une seconde fois.
                        # Même piège que les 504 de /fai et /uisp/assign.
                        unanswered = True
                        break
            except httpx.HTTPStatusError as exc:
                raise PowerControlError(
                    f"Commande refusée par le boîtier (HTTP {exc.response.status_code})."
                ) from exc

        return {
            "outputs_cycled": len(bodies),
            "off_seconds": _POWER_CYCLE_OFF_SECONDS,
            "unanswered": unanswered,
        }


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


# ─────────────────────────────────────────────────────────────────────────
# Façade de pilotage — ce que l'API appelle
#
# Même forme que `poll_uisp_power` (host + credentials, pas d'objet ORM) :
# le service ne connaît pas la base, l'endpoint lui passe ce qu'il a lu sur
# la fiche de l'équipement.
# ─────────────────────────────────────────────────────────────────────────


async def get_power_output_state(
    host: str,
    username: str,
    password: str,
    port: int = 443,
) -> dict:
    """Lit l'état de la sortie DC. Lève `PowerControlError` en cas d'échec."""
    client = UISPPowerClient(host, username, password, port)
    return await client.get_power_control_state()


async def control_power_output(
    host: str,
    username: str,
    password: str,
    port: int = 443,
    action: str = "cycle",
) -> dict:
    """Applique une action d'alimentation : `cycle`, `off` ou `on`.

    ⚠️ `cycle` et `off` ne sont pas deux intensités du même geste : `cycle`
    est rendu à son état initial par le firmware au bout de quelques
    secondes, `off` ne l'est par personne. C'est pourquoi l'action est un
    verbe explicite et non un booléen `enabled` — un appelant qui hésite
    entre true et false ne peut pas tomber par accident sur la coupure dont
    on ne revient pas tout seul.
    """
    client = UISPPowerClient(host, username, password, port)
    if action == "cycle":
        return await client.power_cycle_dc_outputs()
    if action == "off":
        return await client.set_dc_outputs_enabled(False)
    if action == "on":
        return await client.set_dc_outputs_enabled(True)
    raise PowerControlError(f"Action d'alimentation inconnue : {action!r}")
