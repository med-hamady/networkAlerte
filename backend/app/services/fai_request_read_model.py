"""Read request history across deployed schemas without migrating the database.

Use scalar projections rather than ORM entities: loading an LR entity also loads
its Rocket relationship and newer Device columns that older deployments lack.
"""

from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import DateTime, inspect, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, Lr
from app.models.fai_request import FaiRequest, FaiRequestEvent


REQUEST_COLUMNS = (
    "id", "device_id", "request_key", "mac", "name", "site", "source",
    "agent", "mode", "reason", "received_at", "completed_at", "active",
    "closed_at", "closed_reason", "actions",
)
EVENT_COLUMNS = ("id", "request_id", "timestamp", "target", "kind", "result", "message")
CONNECTIVITY_COLUMNS = (
    "connectivity_observed_at", "offline_detected_at", "online_detected_at",
)
TABLE_NAMES = ("fai_requests", "fai_request_events", "devices", "lrs")


@dataclass(frozen=True)
class ReadSchema:
    columns: dict[str, set[str]]

    @property
    def request_history(self) -> bool:
        return set(REQUEST_COLUMNS).issubset(self.columns.get("fai_requests", set()))

    @property
    def event_history(self) -> bool:
        return self.request_history and set(EVENT_COLUMNS).issubset(
            self.columns.get("fai_request_events", set()),
        )

    @property
    def connectivity_timestamps(self) -> bool:
        return set(CONNECTIVITY_COLUMNS).issubset(self.columns.get("devices", set()))


async def read_schema(db: AsyncSession) -> ReadSchema:
    """Inspect actual tables; permission and connection failures propagate.

    PostgreSQL's catalog inspector can see table definitions even when SELECT is
    denied. That distinguishes an absent table from a real authorization error.
    """
    connection = await db.connection()
    schema = "public" if connection.dialect.name == "postgresql" else None
    metadata = await connection.run_sync(
        lambda conn: inspect(conn).get_multi_columns(schema=schema, filter_names=TABLE_NAMES),
    )
    return ReadSchema({
        table: {column["name"] for column in columns}
        for (_, table), columns in metadata.items()
    })


async def read_requests(db: AsyncSession, schema: ReadSchema) -> list[SimpleNamespace]:
    if not schema.request_history:
        return []
    table = FaiRequest.__table__
    optional = [name for name in ("identification", "waiting_periods")
                if name in schema.columns["fai_requests"]]
    query = select(*(table.c[name] for name in (*REQUEST_COLUMNS, *optional))).order_by(
        table.c.received_at.desc(), table.c.id.desc(),
    )
    result = await db.execute(query)
    requests = []
    for row in result.mappings():
        values = dict(row)
        values["identification"] = values.get("identification") or {}
        values["waiting_periods"] = values.get("waiting_periods") or []
        values["actions"] = values.get("actions") or []
        requests.append(SimpleNamespace(**values))
    return requests


async def read_lrs(db: AsyncSession, schema: ReadSchema) -> list[SimpleNamespace]:
    device = Device.__table__
    lr = Lr.__table__
    columns = [device.c.id, device.c.mac_address, device.c.status,
               lr.c.client_blocked, lr.c.unblock_pending]
    available = schema.columns.get("devices", set())
    columns.extend(
        device.c[name] if name in available else
        literal(None, type_=DateTime(timezone=True)).label(name)
        for name in CONNECTIVITY_COLUMNS
    )
    result = await db.execute(select(*columns).select_from(
        device.join(lr, device.c.id == lr.c.id),
    ))
    return [SimpleNamespace(**dict(row)) for row in result.mappings()]


async def read_request_events(db: AsyncSession, request_key: str):
    """Return None for an unknown request; callers guard schema availability."""
    request_table = FaiRequest.__table__
    request = (await db.execute(select(request_table.c.id, request_table.c.actions).where(
        request_table.c.request_key == request_key,
    ))).mappings().one_or_none()
    if request is None:
        return None
    event_table = FaiRequestEvent.__table__
    events = (await db.execute(select(*(event_table.c[name] for name in EVENT_COLUMNS)).where(
        event_table.c.request_id == request["id"],
    ).order_by(event_table.c.timestamp, event_table.c.id))).mappings().all()
    return request["actions"] or [], events
