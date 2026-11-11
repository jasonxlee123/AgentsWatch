#!/usr/bin/env python3
"""Adopt a legacy docker-initdb local database into the migration ledger.

Older local volumes were built by mounting migration SQL files directly into
PostgreSQL's init directory and therefore have no migration ledger. This
one-time bridge is deliberately restricted to the ``agentswatch_local``
database and verifies a concrete schema marker for every adopted migration.
It never changes application data or pretends that a missing migration ran.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.apply_core_asset_migrations import (  # noqa: E402
    LEDGER_TABLE,
    LOCK_NAME,
    applied_migrations,
    discover_migrations,
    existing_managed_schemas,
    ledger_exists,
    validate_history,
)


MARKERS = {
    "001": """
        SELECT to_regclass('org.department') IS NOT NULL
           AND to_regclass('org.person') IS NOT NULL
           AND to_regclass('asset.agent_asset') IS NOT NULL
           AND to_regclass('audit.change_log') IS NOT NULL
    """,
    "002": """
        SELECT to_regclass('asset.server_connection') IS NOT NULL
           AND to_regclass('automation.operation_run') IS NOT NULL
    """,
    "004": """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'asset' AND table_name = 'runtime_instance'
              AND column_name = 'management_driver'
        )
    """,
    "005": "SELECT to_regclass('asset.runtime_web_endpoint') IS NOT NULL",
    "006": "SELECT to_regclass('asset.agent_channel_configuration') IS NOT NULL",
    "007": """
        SELECT to_regclass('asset.agent_channel_configuration') IS NOT NULL
           AND to_regprocedure('asset.validate_primary_agent_channel()') IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgrelid = 'asset.agent_channel_configuration'::regclass
                 AND tgname = 'trg_agent_channel_primary' AND NOT tgisinternal
           )
    """,
    "008": """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'automation' AND table_name = 'operation_host_result'
              AND column_name = 'agent_id'
        )
    """,
    "009": "SELECT to_regclass('automation.automation_definition') IS NOT NULL",
    "010": "SELECT to_regclass('automation.operation_event') IS NOT NULL",
    "011": """
        SELECT to_regclass('asset.ix_server_public_ip_current') IS NOT NULL
           AND to_regclass('asset.uq_server_public_ip_current') IS NULL
    """,
    "012": """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'asset' AND table_name = 'runtime_instance'
              AND column_name = 'command_path'
        ) AND EXISTS (
            SELECT 1 FROM pg_enum enum_value
            JOIN pg_type enum_type ON enum_type.oid = enum_value.enumtypid
            JOIN pg_namespace namespace ON namespace.oid = enum_type.typnamespace
            WHERE namespace.nspname = 'asset'
              AND enum_type.typname = 'runtime_management_driver'
              AND enum_value.enumlabel = 'systemd_user'
        )
    """,
    "013": "SELECT to_regclass('security.user_account') IS NOT NULL",
    "014": """
        SELECT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgrelid = 'org.department'::regclass
              AND tgname = 'trg_department_identity_guard' AND NOT tgisinternal
        ) AND EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgrelid = 'org.person'::regclass
              AND tgname = 'trg_person_identity_guard' AND NOT tgisinternal
        )
    """,
    "015": """
        SELECT to_regclass('asset.uq_runtime_one_active_primary_agent') IS NOT NULL
           AND to_regclass('integration.observability_gateway_v1') IS NOT NULL
    """,
    "016": """
        SELECT to_regclass('asset.runtime_group_member') IS NOT NULL
           AND EXISTS (
               SELECT 1 FROM pg_enum enum_value
               JOIN pg_type enum_type ON enum_type.oid = enum_value.enumtypid
               JOIN pg_namespace namespace ON namespace.oid = enum_type.typnamespace
               WHERE namespace.nspname = 'asset'
                 AND enum_type.typname = 'group_resource_type'
                 AND enum_value.enumlabel = 'runtime'
           )
    """,
    "017": """
        SELECT to_regclass('security.server_ssh_credential') IS NOT NULL
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'asset' AND table_name = 'server_connection'
                 AND column_name = 'bootstrap_status'
           )
    """,
    "018": "SELECT to_regclass('security.api_key') IS NOT NULL",
    "019": "SELECT to_regclass('security.operation_confirmation') IS NOT NULL",
    "020": "SELECT to_regclass('integration.observability_gateway_v2') IS NOT NULL",
    "021": """
        SELECT to_regclass('asset.uq_agent_identity_structure_role') IS NOT NULL
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'asset' AND table_name = 'agent_deployment'
                 AND column_name = 'primary_agent_structure_role'
           )
           AND EXISTS (
               SELECT 1 FROM pg_constraint
               WHERE conrelid = 'asset.agent_deployment'::regclass
                 AND conname = 'fk_agent_deployment_primary_role'
           )
    """,
}


def admin_url() -> str:
    value = os.environ.get("AGENTSWATCH_MIGRATION_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("AGENTSWATCH_MIGRATION_DATABASE_URL is required")
    return value


def detect_applied_prefix(connection, migrations):
    versions = [migration.version for migration in migrations]
    missing_markers = sorted(set(versions) - set(MARKERS))
    if missing_markers:
        raise RuntimeError(
            "local legacy adoption has no schema marker for migration(s): "
            + ", ".join(missing_markers)
        )
    prefix = []
    found_gap = False
    for migration in migrations:
        present = bool(connection.execute(MARKERS[migration.version]).fetchone()[0])
        if present and found_gap:
            raise RuntimeError(
                "legacy local schema is not an exact migration prefix; "
                f"migration {migration.version} appears after a missing migration"
            )
        if present:
            prefix.append(migration)
        else:
            found_gap = True
    return prefix


def bootstrap(connection) -> int:
    migrations = discover_migrations()
    if connection.execute("SELECT current_database()").fetchone()[0] != "agentswatch_local":
        raise RuntimeError("legacy migration adoption is restricted to agentswatch_local")
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_NAME,))
    if ledger_exists(connection):
        validate_history(migrations, applied_migrations(connection))
        return 0
    managed_schemas = existing_managed_schemas(connection)
    if not managed_schemas:
        return 0
    prefix = detect_applied_prefix(connection, migrations)
    if not prefix or prefix[0].version != "001":
        raise RuntimeError("legacy local schema is present but migration 001 cannot be verified")
    connection.execute(
        f"""
        CREATE TABLE {LEDGER_TABLE} (
            version text PRIMARY KEY,
            filename text NOT NULL UNIQUE,
            sha256 text NOT NULL CHECK (length(sha256) = 64),
            applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            applied_by text NOT NULL DEFAULT current_user
        )
        """
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO {LEDGER_TABLE} (version, filename, sha256) VALUES (%s, %s, %s)",
            [(item.version, item.path.name, item.checksum) for item in prefix],
        )
    validate_history(migrations, applied_migrations(connection))
    return len(prefix)


def main() -> int:
    try:
        with psycopg.connect(admin_url()) as connection:
            adopted = bootstrap(connection)
        if adopted:
            print(f"adopted {adopted} verified legacy local migrations")
        else:
            print("local migration ledger already ready")
        return 0
    except (OSError, psycopg.Error, RuntimeError, ValueError) as exc:
        print(f"local migration ledger bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
