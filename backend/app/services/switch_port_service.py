"""
Auto-detection of which switch port each supervised device is plugged into.

Why this exists
---------------
Port-level switch monitoring (`switch_port_down` / `switch_port_speed_low`) was
gated on `uisp_switches.rocket_port_index`, a column no code ever filled: it is
NULL on every switch, so `port_idx > 0` was false and the whole evaluation —
including the "UP but negotiated below 1 Gb/s" check — never ran anywhere.

Filling that one column by hand would not have been enough either: a site holds
up to SITE_INFRA_MAX (14) infra devices behind a single switch, and one column
can only ever designate one of them.

So instead of naming a port, we discover the wiring: the switch's MAC
forwarding table says which port each MAC was learned on, and we already know
the MAC of every supervised device (it is their identity — see the discovery /
UISP sync services). Matching the two gives `port → device` for every port that
carries something we supervise. Ports with nothing known behind them (unused,
third-party gear) stay unattributed and are never alerted on, which is what
keeps this quiet.

⚠️ Two sources, and the SNMP one is dead on our hardware (2026-07-30)
--------------------------------------------------------------------
The FDB path below is correct but returns NOTHING on this fleet: the UISP
switches implement only IF-MIB — the whole BRIDGE-MIB subtree answers
`NoSuchObject`, and they emit no LLDP/CDP either, so the radio can't learn its
port from the wire (both verified on real hardware, two switch families).

The wiring is instead read from the **UISP controller**, which does know it:
`GET /nms/api/v2.1/data-links` returns `ethernet` links whose switch end carries
`interface.identification.name = "portN"` and `deviceName = "0/N"`. See
`detect_from_uisp` — that is the PRIMARY source. The FDB pass is kept as a
fallback for switches UISP doesn't cover (a third-party switch that does expose
BRIDGE-MIB), and costs nothing when there are none.

Stickiness — the one rule not to break
--------------------------------------
An attribution is NEVER cleared because the MAC stopped being seen. A switch
ages a MAC out of its FDB within minutes of the port going DOWN, so "the MAC
vanished" and "the link just died" are the same observation — and the second is
exactly when the alert must fire. Clearing on absence would therefore make the
feature blind at the only moment it matters. An attribution is only ever
overwritten by a NEWER, positive observation (the device answered on another
port, or behind another switch).
"""

import datetime
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Device, Rocket, UispSwitch
from app.services import snmp_service, uisp_service

logger = logging.getLogger(__name__)

# Device types that can legitimately sit on a switch port. LRs are excluded:
# they are subscriber radios reached over the air, never cabled to a site
# switch — and there are ~1000 of them, which would blow the GET budget.
CABLED_DEVICE_TYPES = ("rocket", "airfiber", "ptp_litebeam", "uisp_power", "uisp_switch")

# Highest ifIndex we will auto-raise a switch's `max_ports` to. Matches the
# ceiling of the switch edit form.
MAX_PORT_INDEX = 64


@dataclass
class SwitchWiring:
    """Outcome of one detection pass on one switch (used for logs and dry-runs)."""

    switch_id: int
    switch_name: str
    source: str = "fdb"  # "uisp" (data-links) or "fdb" (BRIDGE-MIB fallback)
    candidates: int = 0
    # device name -> ifIndex, newly written or confirmed this pass
    attributed: dict[str, int] = field(default_factory=dict)
    # ifIndex -> [device names] for ports carrying several supervised devices
    ambiguous: dict[int, list[str]] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    # ifIndex -> [device names] refused because the switch denies that ifIndex
    # exists (UISP announced a port number the switch's own IF-MIB doesn't have)
    rejected_ports: dict[int, list[str]] = field(default_factory=dict)
    # ifIndex -> ifDescr read live, so an operator can eyeball the numbering
    if_descrs: dict[int, str] = field(default_factory=dict)
    max_ports_raised_to: int | None = None
    rocket_port_index_set: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _candidate_devices(session: AsyncSession, switch: UispSwitch) -> list[Device]:
    """Supervised devices that could be cabled to this switch: same site, has a MAC.

    Scoping by site keeps the SNMP cost at one GET per plausible device (~14 for
    a full site) instead of one per device in the whole fleet, and stops a MAC
    learned through an inter-site link from attributing a device to the wrong
    switch. A switch with no site is skipped by the caller.
    """
    result = await session.execute(
        select(Device).where(
            Device.site == switch.site,
            Device.id != switch.id,
            Device.mac_address.is_not(None),
            Device.device_type.in_(CABLED_DEVICE_TYPES),
        )
    )
    return list(result.scalars().all())


def _write_wiring(
    switch: UispSwitch,
    devices_by_port: dict[int, list[Device]],
    wiring: SwitchWiring,
    dry_run: bool,
) -> None:
    """Apply one switch's `port → device(s)` observation to the ORM objects.

    Shared by both sources (UISP data-links and the SNMP FDB) so the guards
    below hold identically whichever one produced the observation.
    """
    now = datetime.datetime.now(datetime.UTC)
    highest_port = 0
    attributed: list[Device] = []
    for if_index, devices in sorted(devices_by_port.items()):
        if len(devices) > 1:
            wiring.ambiguous[if_index] = sorted(d.name for d in devices)
            # Positive evidence that a previous attribution was wrong — clear
            # it. This is NOT the stickiness case: there we keep an attribution
            # because the MAC merely stopped being seen (which is what a dead
            # link looks like); here the source actively reports several devices
            # on the port, so naming any single one of them in an alert would
            # point the operator at the wrong equipment.
            if not dry_run:
                for device in devices:
                    if device.uplink_switch_id == switch.id:
                        device.uplink_switch_id = None
                        device.uplink_switch_port = None
                        device.uplink_detected_at = None
            continue
        if switch.fiber_port_index and if_index == switch.fiber_port_index:
            # The fibre uplink already has its own dedicated rule
            # (fiber_link_down); watching it twice would double-alert.
            continue
        device = devices[0]
        wiring.attributed[device.name] = if_index
        attributed.append(device)
        highest_port = max(highest_port, if_index)
        if not dry_run:
            device.uplink_switch_id = switch.id
            device.uplink_switch_port = if_index
            device.uplink_detected_at = now

    # A port we proved carries a supervised device must be inside the SNMP scan
    # range, otherwise `port_N_up` is never collected and the port stays
    # invisible — the exact blind spot that hid the fibre SFP at index 25.
    if highest_port > switch.max_ports and highest_port <= MAX_PORT_INDEX:
        wiring.max_ports_raised_to = highest_port
        if not dry_run:
            switch.max_ports = highest_port

    # Keep the legacy operator-facing column meaningful when the answer is
    # unambiguous (exactly one Rocket behind this switch). Never overwrites a
    # value an operator typed: it stays a manual override, and it is evaluated
    # on top of the auto-detected ports.
    if switch.rocket_port_index is None:
        rocket_ports = {
            wiring.attributed[d.name] for d in attributed if isinstance(d, Rocket)
        }
        if len(rocket_ports) == 1:
            only = rocket_ports.pop()
            wiring.rocket_port_index_set = only
            if not dry_run:
                switch.rocket_port_index = only


async def detect_switch_wiring(
    session: AsyncSession,
    switch: UispSwitch,
    snmp_port: int,
    snmp_timeout: int,
    default_community: str,
    dry_run: bool = False,
) -> SwitchWiring:
    """Locate every supervised device behind `switch` and record its port.

    Writes `uplink_switch_id` / `uplink_switch_port` / `uplink_detected_at` on
    the matched devices (unless `dry_run`). Does not commit — the caller owns
    the transaction.
    """
    wiring = SwitchWiring(switch_id=switch.id, switch_name=switch.name)

    if not switch.ip_address:
        wiring.error = "switch sans IP"
        return wiring
    if not switch.site:
        wiring.error = "switch sans site — périmètre de recherche indéterminable"
        return wiring

    candidates = await _candidate_devices(session, switch)
    wiring.candidates = len(candidates)
    if not candidates:
        wiring.error = "aucun équipement supervisé avec MAC sur ce site"
        return wiring

    by_mac = {d.mac_address.strip().lower(): d for d in candidates if d.mac_address}
    located = await snmp_service.resolve_mac_ports(
        host=switch.ip_address,
        macs=list(by_mac),
        community=switch.snmp_community or default_community,
        port=snmp_port,
        timeout=snmp_timeout,
    )

    # A port carrying several supervised devices is an uplink or a chained
    # switch, not the port of any one of them — attributing it would blame the
    # wrong device (and, on a chain, a port that isn't even in this switch).
    devices_by_port: dict[int, list[Device]] = {}
    for mac, if_index in located.items():
        device = by_mac.get(mac)
        if device is not None:
            devices_by_port.setdefault(if_index, []).append(device)

    _write_wiring(switch, devices_by_port, wiring, dry_run)
    wiring.unmatched = sorted(d.name for d in candidates if d.mac_address not in located)
    return wiring


# The switch end of a UISP `ethernet` data-link. Only this form is trusted:
#   name "port6" + deviceName "0/6"  → ifIndex 6
# UniFi switches report `ethN` with no deviceName, and their numbering is NOT
# provable from the payload — on the same switch `eth0` is labelled "port1" (0-
# indexed) while `eth14` is labelled "port14" (1-indexed). Guessing would make an
# alert name the wrong physical port, so those links are reported unsupported and
# never attributed.
_UISP_PORT_NAME_RE = re.compile(r"^port(\d+)$", re.IGNORECASE)
_UISP_PORT_DEVNAME_RE = re.compile(r"^\d+/(\d+)$")


def _uisp_port_index(itf: dict) -> int | None:
    """ifIndex from a data-link interface block, or None if the form isn't trusted."""
    ident = itf.get("identification") or {}
    match = _UISP_PORT_NAME_RE.match(str(ident.get("name") or "").strip())
    if not match:
        return None
    index = int(match.group(1))
    # `deviceName` is the switch's own CLI notation ("0/6"). When present it must
    # agree — two independent statements of the same number rather than one.
    dev_match = _UISP_PORT_DEVNAME_RE.match(str(ident.get("deviceName") or "").strip())
    if dev_match and int(dev_match.group(1)) != index:
        return None
    return index if 0 < index <= MAX_PORT_INDEX else None


async def detect_from_uisp(
    session: AsyncSession,
    snmp_port: int,
    snmp_timeout: int,
    default_community: str,
    dry_run: bool = False,
) -> list[SwitchWiring]:
    """Read the wiring from the UISP controller's `ethernet` data-links.

    This is the PRIMARY source: our switches expose no BRIDGE-MIB and emit no
    LLDP, but the controller knows which device sits on which switch port (its
    own agents report it). One call to `/data-links` covers the whole fleet.

    Identity is the **MAC** on both ends — data-links carry UISP device ids, which
    `fetch_devices` translates to MACs, the same identity the rest of the project
    uses. Device NAMES are never matched on: they collide and get edited.

    Numbering is CHECKED, not assumed: every attributed index must answer
    `ifDescr` on the switch, i.e. be a real ifIndex in the same numbering as the
    `port_N_*` metrics. A switch that answers nothing at all keeps its previous
    attributions (stickiness — an unreachable switch has nothing to say); a
    switch that answers but denies a specific index loses that one attribution.
    """
    settings = get_settings()
    if not settings.uisp_base_url:
        return []

    client = uisp_service.UISPClient(
        settings.uisp_base_url,
        username=settings.uisp_username,
        password=settings.uisp_password,
        api_token=settings.uisp_api_token,
        verify_tls=settings.uisp_verify_tls,
        timeout=settings.uisp_request_timeout,
    )
    raw_devices = await client.fetch_devices()
    links = await client.fetch_data_links()

    mac_by_uisp_id: dict[str, str] = {}
    for raw in raw_devices:
        ident = raw.get("identification") or {}
        uisp_id, mac = ident.get("id"), ident.get("mac")
        if uisp_id and mac:
            mac_by_uisp_id[str(uisp_id)] = str(mac).strip().lower()

    switches = (await session.execute(select(UispSwitch))).scalars().all()
    switch_by_mac = {s.mac_address.strip().lower(): s for s in switches if s.mac_address}

    result = await session.execute(
        select(Device).where(
            Device.mac_address.is_not(None),
            Device.device_type.in_(CABLED_DEVICE_TYPES),
        )
    )
    device_by_mac = {
        d.mac_address.strip().lower(): d for d in result.scalars().all() if d.mac_address
    }

    # switch id -> {ifIndex: [devices]}, plus the links we deliberately refused.
    per_switch: dict[int, dict[int, list[Device]]] = {}
    unsupported: list[str] = []
    for link in links:
        if str(link.get("type") or "").lower() != "ethernet":
            continue
        ends = []
        for side in ("from", "to"):
            end = link.get(side) or {}
            uisp_id = ((end.get("device") or {}).get("identification") or {}).get("id")
            mac = mac_by_uisp_id.get(str(uisp_id)) if uisp_id else None
            ends.append((mac, end.get("interface") or {}))

        # Exactly one end must be a switch of ours. Zero = a link between two
        # non-switches (an AF60 cabled straight to a Rocket). Two = an
        # inter-switch uplink: each end is a valid statement ("A is on port P of
        # B" AND "B is on port Q of A"), but a device holds a single uplink
        # column, so picking one would be arbitrary. Both cases: skip.
        switch_sides = [i for i, (mac, _) in enumerate(ends) if mac in switch_by_mac]
        if len(switch_sides) != 1:
            continue
        side = switch_sides[0]
        switch_mac, switch_itf = ends[side]
        peer_mac = ends[1 - side][0]
        switch = switch_by_mac[switch_mac]

        if_index = _uisp_port_index(switch_itf)
        if if_index is None:
            ident = switch_itf.get("identification") or {}
            unsupported.append(f"{switch.name}:{ident.get('name')!r}")
            continue
        device = device_by_mac.get(peer_mac) if peer_mac else None
        if device is None or device.id == switch.id:
            continue
        per_switch.setdefault(switch.id, {}).setdefault(if_index, []).append(device)

    if unsupported:
        logger.warning(
            "Switch port mapping (UISP) — %d lien(s) au format de port non "
            "reconnu, ignoré(s) plutôt que devinés : %s",
            len(unsupported), ", ".join(sorted(set(unsupported))[:12]),
        )

    results: list[SwitchWiring] = []
    for switch in switches:
        devices_by_port = per_switch.get(switch.id)
        if not devices_by_port:
            continue
        wiring = SwitchWiring(
            switch_id=switch.id, switch_name=switch.name, source="uisp",
            candidates=sum(len(v) for v in devices_by_port.values()),
        )
        if switch.ip_address:
            descrs = await snmp_service.fetch_if_descrs(
                host=switch.ip_address,
                indexes=sorted(devices_by_port),
                community=switch.snmp_community or default_community,
                port=snmp_port,
                timeout=snmp_timeout,
            )
            if not descrs:
                # No index answered: the switch is silent, not contradicting us.
                # Keep what it had rather than dropping live monitoring.
                wiring.error = "switch muet en SNMP — attributions précédentes conservées"
                results.append(wiring)
                continue
            wiring.if_descrs = descrs
            for if_index in list(devices_by_port):
                if if_index not in descrs:
                    wiring.rejected_ports[if_index] = [
                        d.name for d in devices_by_port.pop(if_index)
                    ]
        _write_wiring(switch, devices_by_port, wiring, dry_run)
        results.append(wiring)
    return results


async def detect_all(
    session: AsyncSession,
    snmp_port: int,
    snmp_timeout: int,
    default_community: str,
    dry_run: bool = False,
    switch_id: int | None = None,
    skip_switch_ids: set[int] | None = None,
) -> list[SwitchWiring]:
    """Run the FDB (SNMP) detection over every reachable switch (or a single one).

    Fallback source — returns nothing on UISP switches (no BRIDGE-MIB). Use
    `detect_from_uisp` first; this covers a third-party switch the controller
    doesn't know but that does expose its forwarding table. `skip_switch_ids`
    lets the caller exclude the switches the controller already answered for.
    """
    stmt = select(UispSwitch).where(UispSwitch.ip_address.is_not(None))
    if skip_switch_ids:
        stmt = stmt.where(UispSwitch.id.not_in(skip_switch_ids))
    if switch_id is not None:
        stmt = stmt.where(UispSwitch.id == switch_id)
    else:
        # A switch we can't reach has nothing to tell us this pass; its existing
        # attributions stay untouched (stickiness).
        stmt = stmt.where(UispSwitch.status == "up")
    result = await session.execute(stmt)
    switches = list(result.scalars().all())

    return [
        await detect_switch_wiring(
            session, switch, snmp_port, snmp_timeout, default_community, dry_run=dry_run,
        )
        for switch in switches
    ]


async def watched_ports(session: AsyncSession, switch_id: int) -> dict[int, str]:
    """{ifIndex: device name} for every port of this switch we monitor.

    These are the ports proven to carry a supervised device. `rocket_port_index`
    is added by the caller (it is a manual override, valid even with no MAC
    ever learned).
    """
    result = await session.execute(
        select(Device.uplink_switch_port, Device.name).where(
            Device.uplink_switch_id == switch_id,
            Device.uplink_switch_port.is_not(None),
        )
    )
    return {port: name for port, name in result.all() if port}
