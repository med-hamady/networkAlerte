"""NetFlow collector — top client→Internet destinations by operator/CDN.

The edge router (MikroTik) exports NetFlow (already enabled) to this server. A
dedicated long-running process (``RUN_MODE=collector`` →
``app/tasks/collector_runner.py``) runs :func:`run_collector`, which:

1. listens on UDP (``netflow_listen_port``) via an asyncio datagram endpoint;
2. decodes each packet with the pure-Python ``netflow`` lib (v1/v5/v9/IPFIX —
   v9/IPFIX templates are cached across packets; data records seen before their
   template are skipped until it arrives);
3. keeps only **public** destination IPs (drops RFC1918/CGNAT/multicast and our
   own ``netflow_internal_prefixes``) = client→Internet egress;
4. resolves each destination IP to its ASN/operator
   (:mod:`app.services.asn_service`) and accumulates bytes/packets/flows in
   memory keyed by ``(asn, operator)``;
5. every ``netflow_flush_interval_seconds`` flushes the aggregate into
   ``traffic_dest_stats`` (one row per ``(time bucket, ASN)``) and resets.

A UDP listener is permanent (not interval-based), which is why this is its own
container rather than an APScheduler job.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import ipaddress
import logging
from functools import lru_cache

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.traffic_dest_stat import TrafficDestStat
from app.services import asn_service

logger = logging.getLogger(__name__)

# Candidate field names for destination IP / byte / packet counters across
# NetFlow v5/v9/IPFIX as normalised by the `netflow` library.
_DST_KEYS = ("IPV4_DST_ADDR", "IPV6_DST_ADDR", "destinationIPv4Address", "destinationIPv6Address")
_BYTE_KEYS = ("IN_BYTES", "IN_OCTETS", "octetDeltaCount", "octetTotalCount", "IN_OCTET")
_PKT_KEYS = ("IN_PKTS", "IN_PACKETS", "packetDeltaCount", "packetTotalCount")


def _first(data: dict, keys: tuple[str, ...]) -> object | None:
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return None


def _normalize_ip(value: object) -> str | None:
    """A flow's address field may be an int (v5) or a string — return dotted/colon
    notation, or None if it isn't a usable address."""
    if value is None:
        return None
    try:
        if isinstance(value, int):
            return str(ipaddress.ip_address(value))
        return str(ipaddress.ip_address(str(value)))
    except ValueError:
        return None


def _extract_flow(data: dict) -> tuple[str, int, int] | None:
    """Return ``(dst_ip, bytes, packets)`` from a decoded flow's data dict, or None."""
    dst = _normalize_ip(_first(data, _DST_KEYS))
    if dst is None:
        return None
    raw_bytes = _first(data, _BYTE_KEYS)
    raw_pkts = _first(data, _PKT_KEYS)
    try:
        nbytes = int(raw_bytes) if raw_bytes is not None else 0
        npkts = int(raw_pkts) if raw_pkts is not None else 0
    except (TypeError, ValueError):
        nbytes, npkts = 0, 0
    return dst, nbytes, npkts


class _Collector:
    """In-memory NetFlow aggregator with periodic DB flush."""

    def __init__(self, settings) -> None:
        self._settings = settings
        # {"netflow": {}, "ipfix": {}} — template state the decoder mutates.
        self._templates: dict[str, dict] = {"netflow": {}, "ipfix": {}}
        # (asn, operator) -> {"bytes", "packets", "flows"}
        self._agg: dict[tuple[int | None, str | None], dict[str, int]] = {}
        self._internal_nets = self._build_internal_nets(settings.netflow_internal_prefix_list)
        self._packets_seen = 0
        self._flows_kept = 0

    @staticmethod
    def _build_internal_nets(prefixes: list[str]) -> list:
        nets = []
        for p in prefixes:
            try:
                nets.append(ipaddress.ip_network(p, strict=False))
            except ValueError:
                logger.warning("Ignoring invalid NETFLOW_INTERNAL_PREFIXES entry: %s", p)
        return nets

    def _is_internal(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True  # unparseable → drop
        # Non-global covers RFC1918, CGNAT (100.64/10), loopback, link-local,
        # multicast, reserved — none of which is an Internet destination.
        if not addr.is_global:
            return True
        return any(addr in net for net in self._internal_nets)

    def handle_packet(self, data: bytes, addr: tuple) -> None:
        """Decode one UDP packet and fold its public flows into the aggregate.

        Runs in the event loop; decoding a packet (≤ ~30 flows) is cheap. Never
        raises — malformed/unknown input is logged and dropped."""
        self._packets_seen += 1
        try:
            from netflow import parse_packet

            packet = parse_packet(data, self._templates)
        except Exception as exc:  # noqa: BLE001 - UDP faces arbitrary input
            # v9/IPFIX data records before their template, truncated packets,
            # unknown versions… all expected on the wire. Keep the collector up.
            logger.debug("NetFlow decode skipped from %s: %s", addr, exc)
            return

        for flow in getattr(packet, "flows", []):
            extracted = _extract_flow(getattr(flow, "data", {}) or {})
            if extracted is None:
                continue
            dst, nbytes, npkts = extracted
            if self._is_internal(dst):
                continue
            asn, org = _resolve_cached(dst)
            slot = self._agg.setdefault((asn, org), {"bytes": 0, "packets": 0, "flows": 0})
            slot["bytes"] += nbytes
            slot["packets"] += npkts
            slot["flows"] += 1
            self._flows_kept += 1

    def _bucket_start(self) -> datetime.datetime:
        minutes = max(1, self._settings.netflow_bucket_minutes)
        now = datetime.datetime.now(datetime.UTC)
        floored = now - datetime.timedelta(
            minutes=now.minute % minutes,
            seconds=now.second,
            microseconds=now.microsecond,
        )
        return floored

    async def flush(self) -> int:
        """Write the current aggregate to the DB and reset it. Returns row count."""
        if not self._agg:
            return 0
        snapshot, self._agg = self._agg, {}
        bucket = self._bucket_start()
        rows = [
            TrafficDestStat(
                bucket_start=bucket,
                asn=asn,
                as_org=org,
                bytes=v["bytes"],
                packets=v["packets"],
                flows=v["flows"],
            )
            for (asn, org), v in snapshot.items()
        ]
        async with async_session_factory() as session:
            session.add_all(rows)
            await session.commit()
        logger.info(
            "NetFlow flush: %d destination(s) written (bucket %s); %d packets / "
            "%d public flows since boot",
            len(rows), bucket.strftime("%Y-%m-%d %H:%M"),
            self._packets_seen, self._flows_kept,
        )
        return len(rows)

    async def flush_loop(self, stop_event: asyncio.Event) -> None:
        interval = max(10, self._settings.netflow_flush_interval_seconds)
        while not stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001 - never let a DB hiccup kill the loop
                logger.exception("NetFlow flush failed; aggregate kept for next cycle")


@lru_cache(maxsize=100_000)
def _resolve_cached(ip: str) -> tuple[int | None, str | None]:
    """Per-process IP→(asn, operator) cache over asn_service (mmdb lookups)."""
    return asn_service.resolve_asn(ip)


class _NetflowProtocol(asyncio.DatagramProtocol):
    def __init__(self, collector: _Collector) -> None:
        self._collector = collector

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._collector.handle_packet(data, addr)


async def run_collector(stop_event: asyncio.Event) -> None:
    """Listen for NetFlow until ``stop_event`` is set; flush on shutdown."""
    settings = get_settings()
    collector = _Collector(settings)
    loop = asyncio.get_running_loop()

    transport, _ = await loop.create_datagram_endpoint(
        lambda: _NetflowProtocol(collector),
        local_addr=(settings.netflow_listen_host, settings.netflow_listen_port),
    )
    logger.info(
        "NetFlow collector listening on %s:%d (flush every %ds, bucket %dmin)",
        settings.netflow_listen_host, settings.netflow_listen_port,
        settings.netflow_flush_interval_seconds, settings.netflow_bucket_minutes,
    )

    flush_task = asyncio.create_task(collector.flush_loop(stop_event))
    try:
        await stop_event.wait()
    finally:
        flush_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await flush_task
        await collector.flush()  # persist whatever accumulated since last cycle
        transport.close()
        logger.info("NetFlow collector stopped")
