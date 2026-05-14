import datetime

from pydantic import BaseModel


class FlapSubEpisode(BaseModel):
    """One raw down/up cycle that was fused into a merged DowntimeEpisode.

    Populated only when the parent episode has flap_count > 1. Lets the
    UI render each individual flap on the Gantt instead of a single
    merged bar, so unstable equipment is visually distinguishable from
    a clean long outage of the same envelope.
    """

    started_at: datetime.datetime
    ended_at: datetime.datetime | None
    duration_seconds: float


class DowntimeEpisode(BaseModel):
    """One down/up cycle for a network device — after flapping merge.

    `flap_count` > 1 means several raw incidents have been fused into this
    single episode because they were separated by gaps below the merge
    threshold (typically < 5 minutes). The `flaps` list contains the raw
    sub-incidents in that case (empty when flap_count == 1).
    """

    incident_id: int  # ID of the FIRST raw incident in the merged group
    alert_type: str
    severity: str  # warning | critical (max across the merged group)
    started_at: datetime.datetime
    ended_at: datetime.datetime | None
    is_ongoing: bool
    duration_seconds: float
    flap_count: int  # number of raw incidents fused (1 = no flapping)
    flaps: list[FlapSubEpisode]  # constituent raw incidents (empty when flap_count == 1)


class DeviceDowntime(BaseModel):
    """Aggregate downtime for a single network device over the requested window."""

    device_id: int
    device_name: str
    device_ip: str
    device_type: str  # rocket | uisp_switch | uisp_power
    current_status: str  # up | down | unknown

    episodes_count: int  # episodes after merging (what the UI displays)
    raw_episodes_count: int  # raw incident count before merging — flapping signal
    total_downtime_seconds: float  # sum of episode durations CLIPPED to window
    longest_episode_seconds: float
    availability_pct: float  # 100 * (1 − total_clipped_downtime / window_duration)

    episodes: list[DowntimeEpisode]


class DowntimeLogResponse(BaseModel):
    start: datetime.datetime
    end: datetime.datetime
    merge_gap_seconds: int  # threshold used to fuse consecutive episodes
    items: list[DeviceDowntime]
