"""Downtime journal for network infrastructure (Rocket, Switch, UISP Power).

Returns, for a user-selected time window, every infrastructure device that
was down at least once during that window, and for each device the list of
down episodes (start, end, duration).

Client-side LR devices are deliberately excluded — they have their own
view at /lr-health.

Two refinements vs. raw incidents:

- **Episode merging** — raw incidents separated by a gap below
  `merge_gap_seconds` (default 5 min) are fused into a single episode.
  An unstable link flapping 50× in an hour would otherwise look like
  50 distinct outages; with merging it's a single outage tagged as
  flapping (`flap_count > 1`).

- **Availability %** — `100 × (1 − clipped_downtime / window)`. The
  industry-standard operator metric.
"""

import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import (
    AT_AIRMAX_DOWN,
    AT_DEVICE_UNREACHABLE,
    AT_ROCKET_DOWN,
    AT_SWITCH_DOWN,
    AT_UISP_POWER_UNREACH,
)
from app.models.device import Device
from app.models.incident import Incident
from app.schemas.network_uptime import (
    DeviceDowntime,
    DowntimeEpisode,
    DowntimeLogResponse,
    FlapSubEpisode,
)

logger = logging.getLogger(__name__)


# Every alert_type that indicates a network device is fully unreachable.
# LRs are intentionally excluded: a down LR is a client-side outage (power cut /
# unplugged), not our infra, and no longer raises any incident.
_DOWN_ALERT_TYPES: frozenset[str] = frozenset(
    {
        AT_ROCKET_DOWN,
        AT_AIRMAX_DOWN,
        AT_SWITCH_DOWN,
        AT_UISP_POWER_UNREACH,
        AT_DEVICE_UNREACHABLE,
    }
)

_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
_DEFAULT_MERGE_GAP_SECONDS = 300  # 5 min — typical flapping signature


@dataclass
class _MergedEpisode:
    """Internal type holding the result of fusing one or more raw incidents."""

    first_incident_id: int
    alert_type: str
    severity: str
    started_at: datetime.datetime
    ended_at: datetime.datetime | None
    is_ongoing: bool
    duration_seconds: float
    flap_count: int
    # Raw sub-incidents constituting this merged episode. Populated only when
    # flap_count > 1 — for clean single outages we leave it empty to keep
    # responses lean.
    sub_incidents: list[tuple[datetime.datetime, datetime.datetime | None]]


async def get_downtime_log(
    db: AsyncSession,
    start: datetime.datetime,
    end: datetime.datetime,
    merge_gap_seconds: int = _DEFAULT_MERGE_GAP_SECONDS,
) -> DowntimeLogResponse:
    """Return per-device downtime episodes within [start, end]."""
    now = datetime.datetime.now(datetime.UTC)

    # Pull every incident overlapping the window in a single SQL pass.
    # Overlap = detected_at <= end AND (resolved_at IS NULL OR resolved_at >= start).
    q = (
        select(Incident)
        .where(
            Incident.alert_type.in_(_DOWN_ALERT_TYPES),
            Incident.detected_at <= end,
            or_(Incident.resolved_at.is_(None), Incident.resolved_at >= start),
        )
        .order_by(Incident.device_id.asc(), Incident.detected_at.asc())
    )
    incidents = list((await db.execute(q)).scalars().all())

    if not incidents:
        return DowntimeLogResponse(
            start=start, end=end, merge_gap_seconds=merge_gap_seconds, items=[]
        )

    # Load referenced devices in one shot for naming + current status.
    device_ids = {inc.device_id for inc in incidents}
    dev_rows = await db.execute(select(Device).where(Device.id.in_(device_ids)))
    devices = {d.id: d for d in dev_rows.scalars().all()}

    # Group by device.
    by_device: dict[int, list[Incident]] = defaultdict(list)
    for inc in incidents:
        by_device[inc.device_id].append(inc)

    window_seconds = max(1.0, (end - start).total_seconds())

    items: list[DeviceDowntime] = []
    for device_id, dev_incidents in by_device.items():
        dev = devices.get(device_id)
        if dev is None:
            continue

        merged = _merge_episodes(dev_incidents, merge_gap_seconds, now)

        episodes_out: list[DowntimeEpisode] = []
        total_clipped_secs = 0.0
        longest_secs = 0.0

        for ep in merged:
            real_end = ep.ended_at or now

            clipped_start = max(ep.started_at, start)
            clipped_end = min(real_end, end)
            clipped_secs = max(0.0, (clipped_end - clipped_start).total_seconds())
            total_clipped_secs += clipped_secs
            longest_secs = max(longest_secs, ep.duration_seconds)

            # Only expose sub-flaps when merging actually happened — keeps the
            # response lean for the common case of a single clean outage.
            flaps_out: list[FlapSubEpisode] = []
            if ep.flap_count > 1:
                for sub_start, sub_end in ep.sub_incidents:
                    sub_actual_end = sub_end or now
                    flaps_out.append(
                        FlapSubEpisode(
                            started_at=sub_start,
                            ended_at=sub_end,
                            duration_seconds=max(
                                0.0, (sub_actual_end - sub_start).total_seconds()
                            ),
                        )
                    )

            episodes_out.append(
                DowntimeEpisode(
                    incident_id=ep.first_incident_id,
                    alert_type=ep.alert_type,
                    severity=ep.severity,
                    started_at=ep.started_at,
                    ended_at=ep.ended_at,
                    is_ongoing=ep.is_ongoing,
                    duration_seconds=ep.duration_seconds,
                    flap_count=ep.flap_count,
                    flaps=flaps_out,
                )
            )

        # Newest first inside each device — operators expect recent at the top.
        episodes_out.sort(key=lambda e: e.started_at, reverse=True)

        availability = max(0.0, min(100.0, 100.0 * (1.0 - total_clipped_secs / window_seconds)))

        items.append(
            DeviceDowntime(
                device_id=dev.id,
                device_name=dev.name,
                device_ip=dev.ip_address,
                device_type=dev.device_type,
                current_status=dev.status or "unknown",
                episodes_count=len(episodes_out),
                raw_episodes_count=len(dev_incidents),
                total_downtime_seconds=total_clipped_secs,
                longest_episode_seconds=longest_secs,
                availability_pct=availability,
                episodes=episodes_out,
            )
        )

    items.sort(key=lambda i: (-i.total_downtime_seconds, -i.episodes_count, i.device_name))
    return DowntimeLogResponse(
        start=start, end=end, merge_gap_seconds=merge_gap_seconds, items=items
    )


def _merge_episodes(
    incidents: list[Incident],
    merge_gap_seconds: int,
    now: datetime.datetime,
) -> list[_MergedEpisode]:
    """Fuse consecutive incidents separated by less than `merge_gap_seconds`.

    The incident list must be sorted by `detected_at` ascending. Severity of
    a merged group is the max across its members. If any sub-incident is
    still open, the merged episode is marked as ongoing.
    """
    if not incidents:
        return []

    merged: list[_MergedEpisode] = []

    def _flush(state: dict) -> None:
        is_ongoing = state["last_ended_at"] is None
        end_for_duration = state["current_real_end"]
        duration = max(0.0, (end_for_duration - state["started_at"]).total_seconds())
        merged.append(
            _MergedEpisode(
                first_incident_id=state["first_incident_id"],
                alert_type=state["alert_type"],
                severity=state["severity"],
                started_at=state["started_at"],
                ended_at=state["last_ended_at"],
                is_ongoing=is_ongoing,
                duration_seconds=duration,
                flap_count=state["flap_count"],
                sub_incidents=state["sub_incidents"],
            )
        )

    def _new_state(inc: Incident, inc_end: datetime.datetime, inc_sev: str) -> dict:
        return {
            "first_incident_id": inc.id,
            "alert_type": inc.alert_type or "",
            "severity": inc_sev,
            "started_at": inc.detected_at,
            "last_ended_at": inc.resolved_at,
            "current_real_end": inc_end,
            "flap_count": 1,
            "sub_incidents": [(inc.detected_at, inc.resolved_at)],
        }

    state: dict | None = None
    for inc in incidents:
        inc_end = inc.resolved_at or now
        inc_sev = inc.severity or "warning"
        if state is None:
            state = _new_state(inc, inc_end, inc_sev)
            continue

        gap = (inc.detected_at - state["current_real_end"]).total_seconds()
        if gap < merge_gap_seconds:
            state["flap_count"] += 1
            state["last_ended_at"] = inc.resolved_at
            state["current_real_end"] = inc_end
            state["sub_incidents"].append((inc.detected_at, inc.resolved_at))
            if _SEVERITY_RANK.get(inc_sev, 0) > _SEVERITY_RANK.get(state["severity"], 0):
                state["severity"] = inc_sev
        else:
            _flush(state)
            state = _new_state(inc, inc_end, inc_sev)

    if state is not None:
        _flush(state)

    return merged
