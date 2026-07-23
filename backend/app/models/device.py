"""
Device hierarchy — joined-table inheritance.

A row in `devices` is the shared identity (id, name, ip, status, SNMP community,
discovery metadata). Each concrete type (Rocket, Lr, UispPower, UispSwitch) lives
in its own table joined by FK on devices.id and carries only the columns that are
meaningful for that type.

The discriminator column `devices.device_type` tells SQLAlchemy which subclass to
instantiate when loading. Use `select(Rocket)` to get rockets only, or
`select(Device)` to get the union (polymorphic load).
"""

import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Device(Base):
    """Shared identity of any monitored device."""

    __tablename__ = "devices"

    # ix_devices_site is created in raw SQL by migration c0d1e2f3a4b5 (alongside
    # the `site` column's triggers); declared here so alembic autogenerate knows
    # it and won't emit a spurious drop_index (same pattern as DeviceMetric).
    __table_args__ = (
        Index("ix_devices_site", "site"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Volatile, NOT an identity: DHCP churn moves an IP between MACs over time.
    # Kept UNIQUE (one device per IP at any instant) but nullable so a stale
    # binding can be released (set NULL) when the IP migrates to another device.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, unique=True)
    device_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    location: Mapped[str | None] = mapped_column(String(255))
    # Denormalised site name, maintained by DB triggers (migration
    # c0d1e2f3a4b5). An LR inherits its parent Rocket's location; every other
    # device uses its own `location`; fallback 'Sans site'. Read-only from the
    # app's point of view — never assign it in Python, the triggers own it.
    site: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Coordinates PROVISIONED on the device itself (airOS system.latitude /
    # system.longitude), read over SSH by lr_plan_service. NOT a GPS fix: the
    # radios have no working fix (gpsFixed=0), an operator typed these in. NULL
    # means that unit was never provisioned — all three firmware families (LTU,
    # airMAX AC, LiteBeam M5) do carry the key. Deliberately NOT sourced from
    # UISP: the two disagree by up to ~9 km and the device is the chosen source
    # of truth (2026-07-17).
    # ⚠️ Do not confuse with `location` above, which holds the SITE NAME.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    snmp_community: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Auto-discovery metadata — populated for devices reported by a peer (LRs)
    # or when an external scan detects a new device. mac_address is the stable
    # identifier across IP changes and Rocket reassignment.
    mac_address: Mapped[str | None] = mapped_column(String(17), unique=True, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    auto_discovered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-device alert_policy overrides — see services/alert_policy.merge_overrides
    policy_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Reverse relationships from dependent tables. passive_deletes=True relies on
    # ON DELETE CASCADE at the FK level — without it, SQLAlchemy would try to
    # load children and NULL their device_id (NOT NULL → integrity error).
    metrics: Mapped[list["DeviceMetric"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )
    incidents: Mapped[list["Incident"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )
    power_logs: Mapped[list["PowerStatusLog"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )
    alert_states: Mapped[list["AlertState"]] = relationship(  # noqa: F821
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True,
    )

    __mapper_args__ = {
        "polymorphic_on": "device_type",
        "polymorphic_identity": "device",
    }

    def __repr__(self) -> str:
        return f"<{type(self).__name__}(id={self.id}, name={self.name!r}, ip={self.ip_address})>"

    @property
    def rule_category(self) -> str:
        """Coarse-grained category used to pick alert rules and alert types.

        Returns one of: 'ltu_rocket', 'airmax_rocket', 'lr', 'uisp_power',
        'uisp_switch', 'airfiber', 'ptp_litebeam'. Both LTU and Litebeam
        subscribers share 'lr' for now — split later if variant-specific
        thresholds are needed.
        """
        if isinstance(self, Rocket):
            return "airmax_rocket" if self.radio_tech == "airmax" else "ltu_rocket"
        if isinstance(self, Lr):
            return "lr"
        # PtpLiteBeam + AirFiber + UispSwitch + UispPower : device_type == rule_category.
        return self.device_type


class Rocket(Device):
    """LTU Rocket or airMAX Rocket — a base station radio with an HTTPS API."""

    __tablename__ = "rockets"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # "ltu" for LTU Rockets (LTU LR/Instant/Lite peers), "airmax" for airMAX
    # Rockets (Litebeam peers). Polling routines branch on this.
    radio_tech: Mapped[str] = mapped_column(String(20), nullable=False)

    # Manual override of the rocket_client_overload ceiling. When set (not None)
    # it REPLACES the per-family/channel-width formula entirely — the operator
    # pins the maximum client count this AP may serve before it is flagged
    # saturated. NULL = use the auto formula (_rocket_overload_threshold). Useful
    # for APs whose channel width can't be auto-detected (no airOS creds) or when
    # field experience disagrees with the formula. Preserved by the UISP sync.
    max_clients_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Channel width (MHz) as reported by the UISP controller (overview.channelWidth),
    # refreshed by the daily UISP sync. Used as a FALLBACK by the capacity page to
    # compute the client ceiling when the live poll has no width (e.g. the Rocket
    # was unreachable at poll time, or no airOS creds) — so an AP is no longer
    # left "indéterminé" just because a single live poll missed it. The live-polled
    # width (device_metrics) wins when present; this only fills the gap.
    uisp_channel_width_mhz: Mapped[float | None] = mapped_column(Float, nullable=True)

    # HTTPS API credentials (used by ltu_api_service)
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=443)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Reverse: LRs whose parent is this rocket
    lrs: Mapped[list["Lr"]] = relationship(
        back_populates="rocket", foreign_keys="Lr.rocket_id", lazy="selectin",
    )

    __mapper_args__ = {"polymorphic_identity": "rocket", "polymorphic_load": "selectin"}


class Lr(Device):
    """Subscriber radio (LR) — connects to a Rocket. Carries the link metrics."""

    __tablename__ = "lrs"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Specific model variant. LTU family: ltu_lr / ltu_instant / ltu_lite.
    # airMAX family: litebeam_5ac / litebeam_m5.
    model_variant: Mapped[str] = mapped_column(String(30), nullable=False)

    # Parent rocket — nullable while an LR has been discovered but not yet
    # associated, or has been orphaned. SET NULL on rocket delete so the LR
    # row survives.
    rocket_id: Mapped[int | None] = mapped_column(
        ForeignKey("rockets.id", ondelete="SET NULL"), nullable=True,
    )
    rocket: Mapped["Rocket | None"] = relationship(
        back_populates="lrs", foreign_keys=[rocket_id], lazy="selectin",
    )

    # SSH credentials (used by the transit probe — LR pings the internet on demand)
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=22)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Link characteristic reported by the parent Rocket's API.
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Client internet block ────────────────────────────────────────────────
    # Cutting a client = SSH into this LR and shutting its LAN-facing port
    # (`lan_interface`, default eth0). SSH itself reaches the LR through the
    # radio link (ath0 → Rocket → supervisor), so the management plane survives
    # the cut. `client_blocked` is the *intent*; `client_block_enforced_at` is
    # the last time the shutdown was actually re-asserted on the device. They
    # can diverge: intent recorded but device unreachable → the enforcement job
    # keeps retrying. NEVER point lan_interface at ath0/br0 — that would lock
    # the supervisor out of the LR. The earlier `devices.is_suspended` flag was
    # a no-op (no enforcement); this pair is the real mechanism.
    client_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false",
    )
    client_blocked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    client_blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lan_interface: Mapped[str] = mapped_column(
        String(20), default="eth0", nullable=False, server_default="eth0",
    )
    client_block_enforced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # How the block is enforced on the LR:
    #   "full"          → shut `lan_interface` (total internet cut).
    #   "whatsapp_only" → iptables allowlist (DNS + Meta/WhatsApp RETURN, rest
    #                     DROP) so the client keeps WhatsApp while the rest of
    #                     the internet is cut. Touches no interface, so it is
    #                     immune to the lock-out trap that `full` must guard.
    # Persisted so the enforcement job re-asserts the right mechanism after a
    # reboot. Default "full" keeps pre-existing blocked LRs unchanged.
    block_mode: Mapped[str] = mapped_column(
        String(20), default="full", nullable=False, server_default="full",
    )
    # Unblock is enforced too, symmetrically with the block. Set when an unblock
    # was recorded but the LR could not be reached to bring the LAN port back up:
    # the enforcement job retries until it succeeds. Without it, a client who paid
    # while his LR was powered off would stay cut forever — `client_blocked=False`
    # takes him out of the block loop, and nothing else would ever restore him.
    unblock_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false",
    )
    # Why enforcement was ABANDONED on this LR (structural SSH failure: wrong
    # password, host-key mismatch). Retrying those every 120s is pointless — the
    # LR answers, we just cannot log in. The job skips such LRs and a technician
    # must fix the device (or its stored credentials); cleared on the next success.
    # A merely unreachable LR (powered off, radio down) is NOT structural: it keeps
    # being retried, otherwise unplugging the LR would defeat the block entirely.
    block_unenforceable_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    # When the current abandon was recorded. Lets the enforcement job re-attempt
    # an abandoned LR on a slow cadence (client_block_abandon_retry_hours) so a
    # device that has since recovered — re-flashed (new host key, self-healed via
    # MAC re-pin) or fixed out of band — comes back on its own, without a manual
    # unstick. Reset to now on each fresh abandon; cleared with the reason.
    block_unenforceable_since: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # ── Repli routeur ────────────────────────────────────────────────────────
    # Une règle drop est-elle en place sur le MikroTik pour ce client ? C'est le
    # filet de sécurité du blocage : le LR peut être éteint ou refuser le SSH, le
    # routeur de cœur coupe quand même. L'état DÉSIRÉ n'est pas stocké — il se
    # dérive (`client_blocked` ET coupure LR non confirmée, cf.
    # client_block_service._reconcile_router) ; cette colonne mémorise seulement
    # ce qui est POSÉ, pour n'appeler le routeur que sur transition. Sans elle, la
    # réconciliation interrogerait le routeur pour chaque client à chaque cycle.
    router_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false",
    )
    router_blocked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # ── Content block (per-category destination filter) ──────────────────────
    # Independent of `block_mode`: the client stays fully online EXCEPT toward
    # the selected services (e.g. ["tiktok","google"]). Enforced DNS-only on the
    # LR — dnsmasq `address=/<domain>/0.0.0.0` for the union of the categories'
    # domains (catalogue in config), under its OWN dnsmasq marker so it coexists
    # with a `whatsapp_only` block. NULL/[] = no content filtering. Re-asserted
    # every cycle by `enforce_content_blocks` so it survives an LR reboot (which
    # regenerates /etc/dnsmasq.conf). `content_block_enforced_at` = last success.
    # none_as_null=True so Python None persists as SQL NULL (the terminal "no
    # content filter" state the enforce query filters on with isnot(None)) —
    # NOT the JSON scalar 'null', which would keep the row in the enforce set.
    blocked_categories: Mapped[list | None] = mapped_column(
        JSON(none_as_null=True), nullable=True,
    )
    content_block_enforced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # Direction of the filter above:
    #   "denylist"  → allow everything EXCEPT `blocked_categories` (the default).
    #   "allowlist" → block everything EXCEPT `blocked_categories`.
    # Same column list, opposite policy — which is why the SSH layer stamps the
    # mode into its dnsmasq marker, so flipping direction forces a rewrite.
    content_block_mode: Mapped[str] = mapped_column(
        String(10), default="denylist", nullable=False, server_default="denylist",
    )
    # Router vs bridge mode — read from each LR's HTTP poll (airMAX: airOS
    # status.cgi host.netrole; LTU: Rocket API peer.remote.netMode), no SSH.
    # The client-block feature only works in router mode (the LR must be in
    # the IP path of the client). In bridge mode (L2-transparent), iptables
    # FORWARD and the local dnsmasq are bypassed; the block endpoint refuses
    # with a clear message and the UI surfaces a misconfig badge.
    # Values: "router" | "bridge" | "unknown" (detection not yet run).
    topology_mode: Mapped[str] = mapped_column(
        String(10), default="unknown", nullable=False, server_default="unknown",
    )

    # ── Subscription plan (forfait) ──────────────────────────────────────────
    # The customer's plan is provisioned on the LR as an airOS traffic shaper
    # (egress rate cap per interface in /tmp/system.cfg), NOT exposed by any
    # HTTP API — `lr_plan_service` reads it over SSH and caches the down/up caps
    # here so the frontend shows "20/10 Mbps" without an SSH round-trip per view.
    # `plan_synced_at` is the last successful read (None = never synced). The
    # commercial plan *name* is not on the device (CRM-only). Both caps None
    # after a sync = the LR has no shaper configured (no forfait on the device).
    plan_download_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    plan_upload_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    plan_synced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── UISP controller snapshot (resilient to outages) ──────────────────────
    # Mirror of what the UISP controller knows about this client station,
    # imported by the daily `sync_uisp_stations`. These survive a Rocket/LR
    # outage (the controller keeps the last-known state), so /access can show a
    # client and its bridge/router mode even when our own live poll has nothing.
    # The sync NEVER writes the live-polled columns (`topology_mode`, IP,
    # `rocket_id`, block state) — those stay owned by discovery_service / the
    # live jobs. `topology_mode` (live) wins when known; `uisp_mode` is fallback.
    # `uisp_status` is the controller's view: "active" | "disconnected".
    uisp_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    uisp_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uisp_last_seen: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    uisp_ap_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uisp_synced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __mapper_args__ = {"polymorphic_identity": "lr", "polymorphic_load": "selectin"}


class UispPower(Device):
    """UISP Power — battery-backed PoE PDU with a REST API."""

    __tablename__ = "uisp_powers"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    api_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_port: Mapped[int] = mapped_column(default=443)

    __mapper_args__ = {"polymorphic_identity": "uisp_power", "polymorphic_load": "selectin"}


class UispSwitch(Device):
    """UISP managed switch — monitored via SNMP (no API credentials)."""

    __tablename__ = "uisp_switches"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    max_ports: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    # SNMP ifIndex of the port connected to the supervised Rocket. None = no
    # specific port monitored (we only check the device as a whole).
    rocket_port_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port_min_speed_mbps: Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)

    __mapper_args__ = {"polymorphic_identity": "uisp_switch", "polymorphic_load": "selectin"}


class AirFiber(Device):
    """airFiber 60 (AF60-LR) — lien backhaul point-à-point 60 GHz.

    Équipement d'infrastructure ajouté manuellement (comme UispSwitch / UispPower).
    Parle la MÊME UDAPI que les LTU (POST /api/auth → utoken, GET /api/v1.0/statistics
    avec header x-auth-token) — d'où la réutilisation des creds `ssh_*` comme
    identifiants d'API HTTP, exactement comme Rocket. Lien point-à-point : un seul
    peer (l'autre extrémité), pas d'auto-découverte de CPE.
    """

    __tablename__ = "airfibers"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Identifiants de l'API HTTP locale (consommés par af60_api_service via le
    # client LTU partagé). Même convention de nommage que Rocket.
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=443)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Distance du lien (m), synchronisée depuis l'API pour l'affichage UI.
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    __mapper_args__ = {"polymorphic_identity": "airfiber", "polymorphic_load": "selectin"}


class PtpLiteBeam(Device):
    """LiteBeam (airMAX) faisant un lien point-à-point inter-sites.

    Ce N'EST PAS un Rocket (station de base servant des abonnés) ni un LR
    (abonné). C'est un airMAX en mode PTP (UISP `overview.wirelessMode` =
    `ap-ptp` ou `sta-ptp`), aux deux bouts du lien. Supervisé EXACTEMENT comme un
    backhaul : pollé par `airos_api_poll_job` (login.cgi + status.cgi, mêmes creds
    `ssh_*` que les LR airMAX), capacité de lien évaluée par `p2p_link_substandard`,
    exclu de la capacité clients, affiché dans la section liens inter-sites.
    Identité = MAC. Aucune auto-découverte de CPE (lien à un seul peer).
    """

    __tablename__ = "ptp_litebeams"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Creds de l'API airOS locale (consommés par airos_api_service).
    ssh_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(default=443)
    ssh_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Distance du lien (m), pour l'affichage UI.
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    __mapper_args__ = {"polymorphic_identity": "ptp_litebeam", "polymorphic_load": "selectin"}


class ClientModem(Device):
    """Customer-side modem (TP-Link, Huawei, ZTE, ...) behind an LR.

    The modem sits in the client LAN behind the LR's NAT, so it's not directly
    reachable from the supervisor. It is inventoried and its reachability is
    probed from the parent LR (the ping-from-LR diagnostic). There is no
    interactive shell — most customer modems expose a web UI only.
    """

    __tablename__ = "client_modems"

    id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)

    # Parent LR — provides the SSH jump host. SET NULL on LR delete so the
    # modem row survives orphaned (operator can re-link via PUT).
    lr_id: Mapped[int | None] = mapped_column(
        ForeignKey("lrs.id", ondelete="SET NULL"), nullable=True,
    )
    lr: Mapped["Lr | None"] = relationship(foreign_keys=[lr_id], lazy="selectin")

    # Vestigial inventory metadata — no feature uses these since the
    # interactive shell was removed. Kept to avoid a destructive migration.
    management_protocol: Mapped[str] = mapped_column(String(10), default="ssh", nullable=False)
    management_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    management_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    management_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    management_host_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __mapper_args__ = {"polymorphic_identity": "client_modem", "polymorphic_load": "selectin"}
