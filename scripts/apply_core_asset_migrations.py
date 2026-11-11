#!/usr/bin/env python3
"""Apply ordered AgentsWatch PostgreSQL migrations exactly once.

The database URL is read from the environment so credentials never need to be
placed in a command line. This runner intentionally excludes the local-only
003 role/bootstrap script.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = ROOT / "database" / "core-assets"
MIGRATION_NAME = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")
LOCAL_ONLY_MIGRATIONS = {"003_local_reader.sql"}
LEDGER_TABLE = "public.agentswatch_schema_migration"
LOCK_NAME = "agentswatch-core-assets-migrations"
MANAGED_SCHEMAS = (
    "org", "asset", "audit", "ingest", "automation", "security", "integration",
)


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str
    contents: bytes


def discover_migrations(directory: Path = MIGRATION_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in LOCAL_ONLY_MIGRATIONS:
            continue
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"invalid migration filename: {path.name}")
        contents = path.read_bytes()
        migrations.append(
            Migration(
                version=match.group("version"),
                path=path,
                checksum=hashlib.sha256(contents).hexdigest(),
                contents=contents,
            )
        )
    versions = [item.version for item in migrations]
    if not migrations or versions[0] != "001" or len(versions) != len(set(versions)):
        raise ValueError("core asset migrations must start at 001 and use unique versions")
    return migrations


def migration_sql_contents(contents: bytes, filename: str) -> str:
    try:
        lines = contents.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"migration is not valid UTF-8: {filename}") from exc
    lines = [line for line in lines if not line.lstrip().startswith("\\set ")]
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty:
        raise ValueError(f"empty migration: {filename}")
    first, last = nonempty[0], nonempty[-1]
    if lines[first].strip().upper() != "BEGIN;" or lines[last].strip().upper() != "COMMIT;":
        raise ValueError(f"migration must have an outer BEGIN/COMMIT wrapper: {filename}")
    del lines[last]
    del lines[first]
    return "\n".join(lines).strip() + "\n"


def migration_sql(path: Path) -> str:
    """Parse one migration from a single immutable byte read."""
    return migration_sql_contents(path.read_bytes(), path.name)


def database_url() -> str:
    value = (
        os.environ.get("AGENTSWATCH_MIGRATION_DATABASE_URL", "").strip()
        or os.environ.get("AGENTSWATCH_WRITE_DATABASE_URL", "").strip()
    )
    if not value:
        raise RuntimeError(
            "set AGENTSWATCH_MIGRATION_DATABASE_URL (preferred) or "
            "AGENTSWATCH_WRITE_DATABASE_URL"
        )
    return value


def ledger_exists(connection: psycopg.Connection) -> bool:
    return connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS present", (LEDGER_TABLE,)
    ).fetchone()[0]


def existing_managed_schemas(connection: psycopg.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY nspname",
        (list(MANAGED_SCHEMAS),),
    ).fetchall()
    return [row[0] for row in rows]


def applied_migrations(connection: psycopg.Connection) -> dict[str, tuple[str, str]]:
    if not ledger_exists(connection):
        return {}
    rows = connection.execute(
        f"SELECT version, filename, sha256 FROM {LEDGER_TABLE} ORDER BY version"
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def validate_history(migrations: list[Migration], applied: dict[str, tuple[str, str]]) -> None:
    known = {item.version: item for item in migrations}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise RuntimeError(f"database contains unknown migration versions: {', '.join(unknown)}")
    expected_prefix = [item.version for item in migrations[: len(applied)]]
    actual_versions = list(applied)
    if actual_versions != expected_prefix:
        raise RuntimeError(
            "database migration history is not an exact ordered prefix; "
            f"expected {expected_prefix!r}, found {actual_versions!r}"
        )
    for version, (filename, checksum) in applied.items():
        expected = known[version]
        if filename != expected.path.name or checksum != expected.checksum:
            raise RuntimeError(
                f"migration {version} differs from the already-applied file; "
                "restore the original migration instead of editing history"
            )


def print_plan(migrations: list[Migration], applied: dict[str, tuple[str, str]]) -> None:
    for item in migrations:
        state = "applied" if item.version in applied else "pending"
        print(f"{item.version} {state:7} {item.path.name}")


def apply_migrations(connection: psycopg.Connection, migrations: list[Migration]) -> int:
    connection.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
    try:
        applied = applied_migrations(connection)
        validate_history(migrations, applied)
        if not applied and not ledger_exists(connection):
            collisions = existing_managed_schemas(connection)
            if collisions:
                raise RuntimeError(
                    "refusing an untracked migration over existing managed schemas: "
                    + ", ".join(collisions)
                )
        with connection.transaction():
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
                    version text PRIMARY KEY,
                    filename text NOT NULL UNIQUE,
                    sha256 text NOT NULL CHECK (length(sha256) = 64),
                    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                    applied_by text NOT NULL DEFAULT current_user
                )
                """
            )

        applied_count = 0
        for item in migrations:
            if item.version in applied:
                continue
            with connection.transaction():
                connection.execute(migration_sql_contents(item.contents, item.path.name))
                connection.execute(
                    f"INSERT INTO {LEDGER_TABLE} (version, filename, sha256) VALUES (%s, %s, %s)",
                    (item.version, item.path.name, item.checksum),
                )
            print(f"applied {item.version} {item.path.name}")
            applied_count += 1
        return applied_count
    finally:
        connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        action="store_true",
        help="show applied and pending migrations without changing the database",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    migrations = discover_migrations()
    try:
        with psycopg.connect(database_url(), autocommit=True) as connection:
            server_version = int(connection.execute("SHOW server_version_num").fetchone()[0])
            if server_version < 160000:
                raise RuntimeError("AgentsWatch requires PostgreSQL 16 or newer")
            applied = applied_migrations(connection)
            validate_history(migrations, applied)
            if args.plan:
                if not applied and not ledger_exists(connection):
                    collisions = existing_managed_schemas(connection)
                    if collisions:
                        raise RuntimeError(
                            "untracked managed schemas already exist: " + ", ".join(collisions)
                        )
                print_plan(migrations, applied)
                return 0
            count = apply_migrations(connection, migrations)
            print(f"core asset migrations ready: {count} applied, {len(migrations) - count} unchanged")
            return 0
    except (psycopg.Error, OSError, RuntimeError, ValueError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
