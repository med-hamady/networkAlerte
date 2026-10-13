"""Inspect the configured company database without changing data or migrations.

Run from /app: python scripts/check_company_database.py
Exit 0 means the checked schema, Alembic heads and application privileges are ready.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg


def local_application_tables() -> tuple[str, ...]:
    """Read literal ORM table declarations without importing the application.

    All current models declare a literal __tablename__. Scanning their source
    includes models not exported by app.models, such as users and auth_sessions,
    without creating an engine or relying on which API modules imported them.
    """
    model_dir = Path(__file__).resolve().parents[1] / "app" / "models"
    tables = set()
    for path in model_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "__tablename__"
                for target in node.targets
            ):
                if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                    raise ValueError("The database checker requires literal ORM table names")
                tables.add(node.value.value)
    if not tables:
        raise ValueError("No application table declarations found")
    return tuple(sorted(tables))


TABLES = ("alembic_version", *local_application_tables())
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
# Alembic's version marker alone does not prove that the two new migrations
# completed. Detect manual stamping / schema drift before ORM queries fail.
REQUIRED_COLUMNS = {
    "devices": (
        "connectivity_observed_at", "offline_detected_at", "online_detected_at",
    ),
    "fai_requests": (
        "id", "created_at", "updated_at", "device_id", "request_key", "mac",
        "name", "site", "source", "agent", "mode", "reason", "received_at",
        "completed_at", "active", "closed_at", "closed_reason", "actions",
        "identification", "waiting_periods",
    ),
    "fai_request_events": (
        "id", "created_at", "updated_at", "request_id", "timestamp", "target",
        "kind", "result", "message",
    ),
}
# Exact identities used by the API and its site-resolution helper. Checking only
# a function name could accidentally accept an obsolete overload.
FUNCTION_ARGUMENTS = {
    "fn_dashboard_summary": "",
    "fn_network_health": "timestamp with time zone, timestamp with time zone, integer",
    "fn_site_overview": "",
    "fn_access_clients": "text, text, integer",
    "fn_site_outage_summary": "timestamp with time zone, timestamp with time zone, integer",
    "fn_resolve_site": "integer",
}
FUNCTIONS = tuple(FUNCTION_ARGUMENTS)


def local_migration_heads() -> list[str]:
    """Read the revision files only; never import or execute alembic/env.py."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def legacy_application_columns() -> dict[str, tuple[str, ...]]:
    """Physical ORM columns used by the m2 reader, checked before startup.

    Joined Device subclasses store their own columns; base timestamps belong to
    devices. Missing observation fields are SQL NULL expressions in this reader.
    """
    contracts = {"alembic_version": ("version_num",)}
    model_dir = Path(__file__).resolve().parents[1] / "app" / "models"
    for path in model_dir.glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.ClassDef):
                continue
            table = next((statement.value.value for statement in node.body
                          if isinstance(statement, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "__tablename__"
                                  for target in statement.targets)), None)
            if not table or table in {"fai_requests", "fai_request_events"}:
                continue
            columns = set() if any(isinstance(base, ast.Name) and base.id == "Device" for base in node.bases) else {"id", "created_at", "updated_at"}
            for statement in node.body:
                if (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
                        and isinstance(statement.value, ast.Call) and isinstance(statement.value.func, ast.Name)
                        and statement.value.func.id == "mapped_column"):
                    columns.add(statement.target.id)
            contracts[table] = tuple(sorted(columns))
    if set(contracts) != set(TABLES) - {"fai_requests", "fai_request_events"}:
        raise ValueError("Incomplete legacy ORM column contract")
    return contracts


async def inspect_database(
    connection: asyncpg.Connection, local_heads: list[str],
    *, read_only: bool = False, schema_revision: str = "head",
) -> dict[str, Any]:
    """Read metadata and an aggregate count, never subscriber or account rows."""
    if schema_revision not in {"head", "m2c3d4e5f6a7"} or (schema_revision != "head" and not read_only):
        raise ValueError("Unsupported application schema/access mode")
    column_contract = legacy_application_columns() if schema_revision != "head" else REQUIRED_COLUMNS
    required_tables = set(column_contract) if schema_revision != "head" else set(TABLES)
    expected_revisions = [schema_revision] if schema_revision != "head" else local_heads
    async with connection.transaction(isolation="repeatable_read", readonly=True):
        identity = await connection.fetchrow(
            "SELECT current_database() AS database, current_user AS role, "
            "version() AS postgres_version"
        )
        access = dict(await connection.fetchrow(
            "SELECT has_database_privilege(current_user, current_database(), 'CONNECT') "
            "AS database_connect, COALESCE((SELECT has_schema_privilege(current_user, "
            "oid, 'USAGE') FROM pg_catalog.pg_namespace WHERE nspname = 'public'), false) "
            "AS schema_usage"
        ))
        table_rows = await connection.fetch(
            "SELECT required.name AS table_name, c.oid IS NOT NULL AS present, "
            "COALESCE(has_table_privilege(current_user, c.oid, 'SELECT'), false) AS select_ok, "
            "COALESCE(has_table_privilege(current_user, c.oid, 'INSERT'), false) AS insert_ok, "
            "COALESCE(has_table_privilege(current_user, c.oid, 'UPDATE'), false) AS update_ok, "
            "COALESCE(has_table_privilege(current_user, c.oid, 'DELETE'), false) AS delete_ok "
            "FROM unnest($1::text[]) AS required(name) "
            "LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = 'public' "
            "LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid "
            "AND c.relname = required.name AND c.relkind IN ('r', 'p')",
            list(TABLES),
        )
        tables = {row["table_name"]: row["present"] for row in table_rows}
        table_privileges = {
            row["table_name"]: {
                privilege: row[f"{privilege.lower()}_ok"]
                for privilege in (
                    ("SELECT",) if read_only or row["table_name"] == "alembic_version" else TABLE_PRIVILEGES
                )
            }
            for row in table_rows
        }
        column_rows = await connection.fetch(
            "SELECT t.relname AS table_name, a.attname AS column_name "
            "FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class t ON t.oid = a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'public' AND t.relkind IN ('r', 'p') "
            "AND a.attnum > 0 AND NOT a.attisdropped "
            "AND t.relname = ANY($1::text[])",
            list(column_contract),
        )
        found_columns = {(row["table_name"], row["column_name"]) for row in column_rows}
        required_columns = {
            table: {column: (table, column) in found_columns for column in columns}
            for table, columns in column_contract.items()
        }
        function_rows = await connection.fetch(
            "SELECT p.proname, pg_catalog.oidvectortypes(p.proargtypes) AS arguments, "
            "has_function_privilege(current_user, p.oid, 'EXECUTE') AS execute_ok "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = ANY($1::text[])",
            list(FUNCTIONS),
        )
        found_functions = {
            (row["proname"], row["arguments"]): row["execute_ok"] for row in function_rows
        }
        functions = {
            name: (name, arguments) in found_functions
            for name, arguments in FUNCTION_ARGUMENTS.items()
        }
        function_privileges = {
            name: {
                "signature": f"public.{name}({arguments})",
                "EXECUTE": found_functions.get((name, arguments), False),
            }
            for name, arguments in FUNCTION_ARGUMENTS.items()
        }
        # SERIAL sequences require nextval rights (USAGE or UPDATE). IDENTITY
        # columns (dependency type 'i') use table INSERT rights for generated IDs
        # and do not need a separate sequence grant. Never call nextval here.
        sequence_rows = await connection.fetch(
            "SELECT t.relname AS table_name, a.attname AS column_name, "
            "format('%I.%I', sn.nspname, s.relname) AS sequence_name, "
            "has_sequence_privilege(current_user, s.oid, 'USAGE') AS usage_ok, "
            "has_sequence_privilege(current_user, s.oid, 'UPDATE') AS update_ok "
            "FROM pg_catalog.pg_class s "
            "JOIN pg_catalog.pg_namespace sn ON sn.oid = s.relnamespace "
            "JOIN pg_catalog.pg_depend dep ON dep.objid = s.oid "
            "AND dep.classid = 'pg_catalog.pg_class'::regclass "
            "AND dep.refclassid = 'pg_catalog.pg_class'::regclass "
            "AND dep.deptype = 'a' "
            "JOIN pg_catalog.pg_class t ON t.oid = dep.refobjid "
            "JOIN pg_catalog.pg_namespace tn ON tn.oid = t.relnamespace "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid "
            "AND a.attnum = dep.refobjsubid "
            "WHERE s.relkind = 'S' AND tn.nspname = 'public' "
            "AND t.relname = ANY($1::text[])",
            [table for table in TABLES if table != "alembic_version"],
        )
        sequence_privileges = {
            row["sequence_name"]: {
                "table": row["table_name"],
                "column": row["column_name"],
                "USAGE": row["usage_ok"],
                "UPDATE": row["update_ok"],
                "nextval_allowed": row["usage_ok"] or row["update_ok"],
            }
            for row in sequence_rows
        }
        device_count = None
        if access["schema_usage"] and tables["devices"] and table_privileges["devices"]["SELECT"]:
            device_count = await connection.fetchval(
                "SELECT count(*) FROM public.devices"
            )
        remote_versions = []
        if (
            access["schema_usage"] and tables["alembic_version"]
            and table_privileges["alembic_version"]["SELECT"]
        ):
            remote_versions = sorted(
                row["version_num"]
                for row in await connection.fetch(
                    "SELECT version_num FROM public.alembic_version"
                )
            )
    migrations_match = bool(local_heads) and remote_versions == expected_revisions
    privileges_sufficient = (
        all(access.values())
        and all(all(table_privileges[table].values()) for table in required_tables)
        and all(privileges["EXECUTE"] for privileges in function_privileges.values())
        and (read_only or all(privileges["nextval_allowed"] for privileges in sequence_privileges.values()))
    )
    compatible = (
        all(tables[table] for table in required_tables) and all(functions.values()) and migrations_match and privileges_sufficient
        and all(all(columns.values()) for columns in required_columns.values())
    )
    return {
        "mode": "read_only",
        "application_access_mode": "read_only" if read_only else "read_write",
        "application_schema_revision": schema_revision,
        "expected_alembic_revisions": expected_revisions,
        **dict(identity),
        "public_tables": tables,
        "required_columns": required_columns,
        "device_count": device_count,
        "public_functions": functions,
        "access_privileges": access,
        "table_privileges": table_privileges,
        "function_privileges": function_privileges,
        "sequence_privileges": sequence_privileges,
        "application_privileges_sufficient": privileges_sufficient,
        "functions_executed": False,
        "remote_alembic_revisions": remote_versions,
        "local_alembic_heads": local_heads,
        "migrations_match": migrations_match,
        "checked_schema_compatible": compatible,
        "changes_applied": False,
        "migrations_applied": False,
    }


async def main() -> int:
    connection = None
    try:
        import asyncpg

        from app.core.config import get_settings

        settings = get_settings()
        local_heads = local_migration_heads()
        connection = await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_db,
            timeout=10,
            command_timeout=10,
            server_settings={
                "TimeZone": "UTC",
                "application_name": "network-supervisor-company-db-check",
                "default_transaction_read_only": "on",
            },
        )
        schema_revision = getattr(settings, "company_schema_revision", "head")
        if schema_revision != "head":
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
            if ScriptDirectory.from_config(config).get_revision(schema_revision) is None:
                raise ValueError("Configured legacy revision is not part of the local migration chain")
        result = await inspect_database(connection, local_heads, read_only=getattr(settings, "company_read_only", False),
                                        schema_revision=schema_revision)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["checked_schema_compatible"]:
            print(
                "Schéma absent/incomplet, révisions différentes ou droits applicatifs insuffisants. "
                "Aucune modification ni migration exécutée.",
                file=sys.stderr,
            )
            return 1
        return 0
    except Exception as error:
        # Exception messages and connection URLs can contain credentials.
        print(
            f"Diagnostic impossible ({type(error).__name__}). Vérifier la "
            "configuration, le tunnel et les droits de lecture. "
            "Aucune modification ni migration exécutée.",
            file=sys.stderr,
        )
        return 2
    finally:
        if connection is not None:
            try:
                await connection.close(timeout=5)
            except Exception:
                connection.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
