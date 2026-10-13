"""Rehearse m2 -> head using synthetic rows in a disposable Docker database.

Run only through scripts/test-company-migrations.ps1. This is deliberately not
a pytest test: importing ordinary application fixtures must never select a
company database. The harness rejects all non-test connection coordinates.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

EXPECTED_ENV = {
    "SCHEMA_AUDIT_DISPOSABLE": "synthetic-data-only",
    "POSTGRES_HOST": "schema-db",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "schema_audit",
    "POSTGRES_DB": "schema_audit",
    "SCHEDULER_ENABLED": "false",
    "CLIENT_BLOCK_ENFORCEMENT_ENABLED": "false",
    "MIKROTIK_ENABLED": "false",
    "UISP_SYNC_ENABLED": "false",
    "WHATSAPP_ENABLED": "false",
}
BASE = "m2c3d4e5f6a7"
FIRST = "n3d4e5f6a7b8"
HEAD = "o4e5f6a7b8c9"
CONNECTIVITY = ["connectivity_observed_at", "offline_detected_at", "online_detected_at"]


def assert_isolated_configuration() -> None:
    for key, expected in EXPECTED_ENV.items():
        if os.environ.get(key) != expected:
            raise RuntimeError(f"Refusing migration rehearsal: invalid {key}")
    if Path(".env").exists():
        raise RuntimeError("Refusing migration rehearsal: .env must not be mounted")


async def connect():
    import asyncpg

    connection = await asyncpg.connect(
        host="schema-db", port=5432, user="schema_audit", database="schema_audit",
        password=os.environ["POSTGRES_PASSWORD"], timeout=10,
        server_settings={"TimeZone": "UTC", "application_name": "isolated-schema-audit"},
    )
    assert await connection.fetchval("SELECT current_database()") == "schema_audit"
    return connection


async def fingerprint(connection):
    from check_company_database import TABLES

    # Only synthetic rows in this new database; no files or exports are made.
    rows = {}
    for table in TABLES:
        if table in ("alembic_version", "fai_requests", "fai_request_events"):
            continue
        rows[table] = await connection.fetchval(
            f'SELECT COALESCE(jsonb_agg(to_jsonb(t) - $1::text[] ORDER BY id), \'[]\'::jsonb)::text '
            f'FROM public."{table}" t', CONNECTIVITY if table == "devices" else [],
        )
    return rows


async def seed_base():
    connection = await connect()
    try:
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == BASE
        device_id = await connection.fetchval(
            "INSERT INTO devices(name, device_type, status, mac_address, auto_discovered) "
            "VALUES ('Synthetic migration LR', 'lr', 'down', '02:00:00:00:00:01', false) RETURNING id"
        )
        await connection.execute(
            "INSERT INTO lrs(id, model_variant, client_blocked, unblock_pending) "
            "VALUES ($1, 'litebeam_m5', true, true)", device_id,
        )
        await connection.execute(
            "INSERT INTO incidents(device_id, title, severity, status, detected_at) "
            "VALUES ($1, 'Synthetic incident', 'warning', 'open', now())", device_id,
        )
        await connection.execute(
            "INSERT INTO system_settings(key, value) VALUES ('schema_audit', 'unchanged')"
        )
        from app.services.auth_service import hash_password
        await connection.execute(
            "INSERT INTO users(username, password_hash, enabled, profile_id) "
            "VALUES ('synthetic-audit-user', $1, true, "
            "(SELECT id FROM profiles WHERE is_system LIMIT 1))",
            hash_password("synthetic-reader-password"),
        )
        return device_id, await fingerprint(connection)
    finally:
        await connection.close()


async def inspect_stage(revision, baseline):
    from check_company_database import inspect_database

    connection = await connect()
    try:
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == revision
        assert await fingerprint(connection) == baseline, "Existing synthetic business rows changed"
        report = await inspect_database(connection, [HEAD])
        assert report["checked_schema_compatible"] is (revision == HEAD)
        assert report["changes_applied"] is False
        assert report["migrations_applied"] is False
    finally:
        await connection.close()


async def inspect_read_model(revision):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.core.config import get_settings
    from app.services.fai_request_read_model import read_lrs, read_requests, read_schema

    engine = create_async_engine(get_settings().database_url)
    try:
        async with AsyncSession(engine) as db:
            schema = await read_schema(db)
            assert schema.request_history is (revision != BASE)
            lrs = await read_lrs(db, schema)
            assert len(lrs) == 1 and lrs[0].unblock_pending and lrs[0].client_blocked
            assert all(getattr(lrs[0], column) is None for column in CONNECTIVITY)
            requests = await read_requests(db, schema)
            if revision == BASE:
                assert requests == []
            else:
                assert len(requests) == 1
                assert requests[0].identification == {}
                assert requests[0].waiting_periods == []
    finally:
        await engine.dispose()


async def seed_request(device_id):
    connection = await connect()
    try:
        request_id = await connection.fetchval(
            "INSERT INTO fai_requests(device_id, request_key, name, source, mode, received_at, actions) "
            "VALUES ($1, 'synthetic-request', 'Synthetic request', 'test', 'full', now(), '[]') RETURNING id",
            device_id,
        )
        await connection.execute(
            "INSERT INTO fai_request_events(request_id, timestamp, kind, result, message) "
            "VALUES ($1, now(), 'synthetic', 'unverified', 'No equipment was contacted')", request_id,
        )
        return dict(await connection.fetchrow("SELECT * FROM fai_requests WHERE id = $1", request_id))
    finally:
        await connection.close()


async def verify_head(request_before):
    import asyncpg

    connection = await connect()
    try:
        row = dict(await connection.fetchrow("SELECT * FROM fai_requests WHERE id = $1", request_before["id"]))
        assert json.loads(row.pop("identification")) == {}
        assert json.loads(row.pop("waiting_periods")) == []
        assert row == request_before, "o4 changed a pre-existing request"
        assert await connection.fetchval("SELECT count(*) FROM fai_request_events") == 1
        indexes = set(await connection.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename IN ('fai_requests', 'fai_request_events')"
        ))
        assert any(row[0] == "ix_fai_requests_device_active" for row in indexes)
        assert any(row[0] == "ix_fai_request_events_request_time" for row in indexes)
        # PostgreSQL enforces uniqueness; SAVEPOINT rollback leaves the fixture intact.
        try:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO fai_requests(request_key, name, source, mode, received_at, actions) "
                    "VALUES ('synthetic-request', 'Duplicate', 'test', 'full', now(), '[]')"
                )
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("request_key uniqueness is missing")
        transaction = connection.transaction()
        await transaction.start()
        try:
            await connection.execute("DELETE FROM devices WHERE id = $1", request_before["device_id"])
            assert await connection.fetchval("SELECT device_id FROM fai_requests") is None
            await connection.execute("DELETE FROM fai_requests WHERE id = $1", request_before["id"])
            assert await connection.fetchval("SELECT count(*) FROM fai_request_events") == 0
        finally:
            await transaction.rollback()
    finally:
        await connection.close()


def main():
    assert_isolated_configuration()
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_section_option("logger_alembic", "level", "WARNING")
    command.upgrade(config, BASE)
    device_id, baseline = asyncio.run(seed_base())
    asyncio.run(inspect_stage(BASE, baseline))
    asyncio.run(inspect_read_model(BASE))



    asyncio.run(inspect_stage(BASE, baseline))
    command.upgrade(config, FIRST)
    request_before = asyncio.run(seed_request(device_id))
    asyncio.run(inspect_stage(FIRST, baseline))
    asyncio.run(inspect_read_model(FIRST))
    command.upgrade(config, HEAD)
    asyncio.run(inspect_stage(HEAD, baseline))
    asyncio.run(inspect_read_model(HEAD))
    asyncio.run(verify_head(request_before))
    command.upgrade(config, HEAD)  # Idempotent Alembic invocation, no second DDL.
    asyncio.run(inspect_stage(HEAD, baseline))
    command.downgrade(config, FIRST)
    asyncio.run(inspect_stage(FIRST, baseline))
    asyncio.run(inspect_read_model(FIRST))
    command.downgrade(config, BASE)
    asyncio.run(inspect_stage(BASE, baseline))
    asyncio.run(inspect_read_model(BASE))
    command.upgrade(config, HEAD)
    asyncio.run(inspect_stage(HEAD, baseline))
    print(json.dumps({
        "status": "passed", "postgres": "disposable synthetic database only",
        "stages": ["empty -> m2", "m2 -> n3", "n3 -> o4", "o4 -> o4", "o4 -> n3 -> m2", "m2 -> o4"],
        "checks": ["existing rows preserved", "o4 defaults preserve n3 requests", "read model m2/n3/o4",
                   "strict schema gate", "request uniqueness", "foreign keys SET NULL/CASCADE"],
        "production_connected": False,
    }, indent=2))


if __name__ == "__main__":
    main()
