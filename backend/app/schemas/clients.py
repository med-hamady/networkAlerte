"""Pydantic I/O schemas for client consumption.

The consumption roll-up is hierarchical: site → rocket → client. Each level
carries its own download/upload/total so the frontend can render any level
without re-summing. See ``services.consumption_service`` for how the figures
are sourced and aggregated.
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel

Period = Literal["24h", "7d", "30d", "lifetime", "custom"]


class ClientConsumption(BaseModel):
    """Per-client consumption (one LR), customer-centric.

    download_bytes = traffic the customer received (AP→CPE), sourced from
                     the API's `txBytes` on `peer_tx_bytes`.
    upload_bytes   = traffic the customer sent (CPE→AP), sourced from
                     `rxBytes` on `peer_rx_bytes`.
    first_sample_at = oldest sample we have for this LR in the queried
                      window. For the lifetime view this is the meaningful
                      "supervision started on" timestamp.
    """

    device_id: int
    name: str
    ip_address: str | None  # devices.ip_address est NULLABLE (identité LR par MAC)
    rocket_id: int | None
    rocket_name: str | None
    download_bytes: int
    upload_bytes: int
    total_bytes: int
    samples: int
    has_data: bool
    first_sample_at: datetime.datetime | None
    # Subscription plan (forfait) — rate caps cached from the LR's traffic
    # shaper by lr_plan_service. None = never synced or no shaper on the device.
    plan_download_mbps: float | None = None
    plan_upload_mbps: float | None = None


class RocketConsumption(BaseModel):
    """Consumption rolled up to one parent Rocket, with its clients drilled down.

    rocket_id / rocket_name are null for the synthetic "no parent" bucket that
    collects LRs not associated to any Rocket.
    """

    rocket_id: int | None
    rocket_name: str | None
    download_bytes: int
    upload_bytes: int
    total_bytes: int
    client_count: int
    clients: list[ClientConsumption]


class SiteConsumption(BaseModel):
    """Consumption rolled up to one site (= the parent Rocket's location).

    A site groups every Rocket sharing the same `location`; LRs whose Rocket has
    no location (or no Rocket at all) fall into the fallback bucket.
    """

    site: str
    download_bytes: int
    upload_bytes: int
    total_bytes: int
    rocket_count: int
    client_count: int
    rockets: list[RocketConsumption]


class ClientConsumptionResponse(BaseModel):
    period: Period
    period_start: datetime.datetime | None  # null for "lifetime"
    period_end: datetime.datetime
    # Earliest collected_at actually present in DB for this window. When this
    # is later than period_start, the displayed totals only cover the time
    # from data_start to now — useful after a fresh deployment.
    data_start: datetime.datetime | None
    # Hierarchical roll-up: site → rocket → client. Each level is sorted by
    # total_bytes desc. The frontend drills down level by level.
    sites: list[SiteConsumption]
