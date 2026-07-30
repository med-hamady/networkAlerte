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
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, Rocket, UispSwitch
from app.services import snmp_service

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
    candidates: int = 0
    # device name -> ifIndex, newly written or confirmed this pass
    attributed: dict[str, int] = field(default_factory=dict)
    # ifIndex -> [device names] for ports carrying several supervised devices
    ambiguous: dict[int, list[str]] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
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

    now = datetime.datetime.now(datetime.UTC)
    highest_port = 0
    for if_index, devices in sorted(devices_by_port.items()):
        if len(devices) > 1:
            wiring.ambiguous[if_index] = [d.name for d in devices]
            # Positive evidence that a previous attribution was wrong — clear
            # it. This is NOT the stickiness case: there we keep an attribution
            # because the MAC merely stopped being seen (which is what a dead
            # link looks like); here the switch actively tells us the port
            # carries several devices, so naming any single one of them in an
            # alert would point the operator at the wrong equipment.
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
        highest_port = max(highest_port, if_index)
        if not dry_run:
            device.uplink_switch_id = switch.id
            device.uplink_switch_port = if_index
            device.uplink_detected_at = now

    wiring.unmatched = sorted(d.name for d in candidates if d.mac_address not in located)

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
            wiring.attributed[d.name]
            for d in candidates
            if isinstance(d, Rocket) and d.name in wiring.attributed
        }
        if len(rocket_ports) == 1:
            only = rocket_ports.pop()
            wiring.rocket_port_index_set = only
            if not dry_run:
                switch.rocket_port_index = only

    return wiring


async def detect_all(
    session: AsyncSession,
    snmp_port: int,
    snmp_timeout: int,
    default_community: str,
    dry_run: bool = False,
    switch_id: int | None = None,
) -> list[SwitchWiring]:
    """Run the detection over every reachable switch (or a single one)."""
    stmt = select(UispSwitch).where(UispSwitch.ip_address.is_not(None))
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
