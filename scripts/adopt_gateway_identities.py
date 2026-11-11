#!/usr/bin/env python3
"""Safely adopt legacy Gateway IDs into the managed asset database.

The legacy observability inventory contains a mutable host address as well as a
stable ``metadata.server_id``.  This tool deliberately resolves a Runtime only
through the latter and treats host/port merely as fail-closed consistency
checks.  A plan is immutable, contains no database credentials, and must be
confirmed by its exact file SHA-256 before the append-only commit can run.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


DATABASE_URL_ENV = "AGENTSWATCH_GATEWAY_IDENTITY_DATABASE_URL"
PLAN_VERSION = "gateway-identity-adoption-v1"
SOURCE_SYSTEM = "observability_inventory_v1"
SERVER_SOURCE_SYSTEM = "server_inventory_xlsx"
SERVER_IDENTIFIER_TYPE = "server_inventory_id"
GATEWAY_IDENTIFIER_TYPE = "gateway_id"
LOCK_NAME = "agentswatch-gateway-identity-adoption-v1"
ADOPTION_NAMESPACE = uuid.UUID("4e734334-fb86-4947-ada6-74c900f37cce")
GATEWAY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_INVENTORY_BYTES = 10 * 1024 * 1024
MAX_PLAN_BYTES = 20 * 1024 * 1024


class AdoptionRejected(RuntimeError):
    """A fail-closed validation rejected the adoption."""


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value)).strip()


def canonical_key(value: Any) -> str:
    return unicodedata.normalize("NFKC", normalize_text(value)).casefold()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_timestamp(value: Any) -> str:
    text = normalize_text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdoptionRejected(f"invalid timezone-aware timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise AdoptionRejected("timestamp must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    rendered = parsed.isoformat(timespec="microseconds" if parsed.microsecond else "seconds")
    return rendered.replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(ADOPTION_NAMESPACE, f"{kind}:{key}"))


def external_identifier_id(gateway_id: str) -> str:
    return stable_id(
        "external-identifier",
        f"{SOURCE_SYSTEM}:{GATEWAY_IDENTIFIER_TYPE}:{normalize_text(gateway_id)}",
    )


def change_id(identifier_id: str) -> str:
    return stable_id("audit-change", identifier_id)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdoptionRejected(f"JSON contains duplicate object key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, *, maximum_bytes: int, label: str) -> Any:
    path = Path(path)
    if path.is_symlink():
        raise AdoptionRejected(f"{label} must not be a symbolic link: {path}")
    if not path.is_file():
        raise AdoptionRejected(f"{label} does not exist: {path}")
    if path.stat().st_size > maximum_bytes:
        raise AdoptionRejected(f"{label} exceeds the {maximum_bytes}-byte safety limit")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdoptionRejected(f"{label} is not valid UTF-8 JSON: {exc}") from exc


def _normalize_ip(value: Any, *, field: str) -> str:
    text = normalize_text(value)
    if not text:
        raise AdoptionRejected(f"{field} is required")
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError as exc:
        raise AdoptionRejected(f"{field} must be an IP address: {text!r}") from exc


def _normalize_port(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise AdoptionRejected(f"{field} must be an integer port")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise AdoptionRejected(f"{field} must be an integer port") from exc
    if not 1 <= port <= 65535:
        raise AdoptionRejected(f"{field} must be between 1 and 65535")
    return port


def normalize_inventory(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise AdoptionRejected("inventory root must be an object")
    raw_items = payload.get("gateways")
    if not isinstance(raw_items, list):
        raise AdoptionRejected("inventory.gateways must be an array")
    items: list[dict[str, Any]] = []
    gateway_keys: dict[str, int] = {}
    server_keys: dict[str, int] = {}
    for index, raw in enumerate(raw_items):
        label = f"inventory.gateways[{index}]"
        if not isinstance(raw, dict):
            raise AdoptionRejected(f"{label} must be an object")
        gateway_id = normalize_text(raw.get("gateway_id"))
        if not GATEWAY_ID_PATTERN.fullmatch(gateway_id):
            raise AdoptionRejected(f"{label}.gateway_id is invalid: {gateway_id!r}")
        gateway_key = canonical_key(gateway_id)
        if gateway_key in gateway_keys:
            raise AdoptionRejected(
                f"duplicate gateway_id after canonical normalization at rows "
                f"{gateway_keys[gateway_key]} and {index}: {gateway_id!r}"
            )
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            raise AdoptionRejected(f"{label}.metadata must be an object")
        stable_server_id = normalize_text(metadata.get("server_id"))
        stable_server_key = canonical_key(stable_server_id)
        if not stable_server_key:
            raise AdoptionRejected(f"{label}.metadata.server_id is required")
        if stable_server_key in server_keys:
            raise AdoptionRejected(
                f"duplicate metadata.server_id after canonical normalization at rows "
                f"{server_keys[stable_server_key]} and {index}: {stable_server_id!r}"
            )
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise AdoptionRejected(f"{label}.enabled must be a boolean")
        host = _normalize_ip(raw.get("host"), field=f"{label}.host")
        port = _normalize_port(raw.get("port"), field=f"{label}.port")
        gateway_keys[gateway_key] = index
        server_keys[stable_server_key] = index
        items.append(
            {
                "gateway_id": gateway_id,
                "gateway_key": gateway_key,
                "stable_server_id": stable_server_id,
                "stable_server_key": stable_server_key,
                "host": host,
                "port": port,
                "enabled": enabled,
            }
        )
    if not items:
        raise AdoptionRejected("inventory must contain at least one gateway")
    return sorted(items, key=lambda item: (item["stable_server_key"], item["gateway_key"]))


def load_inventory(path: Path) -> tuple[list[dict[str, Any]], str]:
    payload = _load_json(path, maximum_bytes=MAX_INVENTORY_BYTES, label="inventory")
    return normalize_inventory(payload), file_sha256(path)


def database_url(environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    value = normalize_text(values.get(DATABASE_URL_ENV))
    if not value:
        raise AdoptionRejected(f"{DATABASE_URL_ENV} is required")
    return value


def _require_schema(connection: psycopg.Connection) -> None:
    markers = connection.execute(
        """
        SELECT
            to_regclass('asset.server_asset') IS NOT NULL AS has_server,
            to_regclass('asset.runtime_instance') IS NOT NULL AS has_runtime,
            to_regclass('asset.runtime_family') IS NOT NULL AS has_family,
            to_regclass('asset.external_identifier') IS NOT NULL AS has_identifier,
            to_regclass('audit.change_log') IS NOT NULL AS has_audit,
            to_regclass('public.agentswatch_schema_migration') IS NOT NULL AS has_ledger
        """
    ).fetchone()
    if not markers or not all(markers.values()):
        raise AdoptionRejected("database is missing the managed asset/audit schema")


def _rows(connection: psycopg.Connection, query: str, parameters: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def fetch_database_snapshot(connection: psycopg.Connection) -> dict[str, Any]:
    """Read the complete identity-relevant snapshot within the caller transaction."""

    _require_schema(connection)
    metadata = connection.execute(
        "SELECT current_database() AS name, current_setting('server_version_num') AS server_version_num"
    ).fetchone()
    migrations = _rows(
        connection,
        """
        SELECT version, filename, sha256
        FROM public.agentswatch_schema_migration
        ORDER BY version
        """,
    )
    runtimes = _rows(
        connection,
        """
        SELECT
            runtime.runtime_id::text AS runtime_id,
            runtime.server_id::text AS server_id,
            family.family_code,
            runtime.runtime_role::text AS runtime_role,
            host(server.internal_ip) AS monitor_host,
            runtime.endpoint_port AS monitor_port,
            runtime.row_version AS runtime_row_version,
            server.row_version AS server_row_version
        FROM asset.runtime_instance runtime
        JOIN asset.runtime_family family
          ON family.runtime_family_id = runtime.runtime_family_id
        JOIN asset.server_asset server
          ON server.server_id = runtime.server_id
        WHERE family.family_code = 'openclaw'
          AND runtime.runtime_role = 'gateway'
        ORDER BY runtime.runtime_id
        """,
    )
    server_identifiers = _rows(
        connection,
        """
        SELECT
            identifier.external_identifier_id::text AS external_identifier_id,
            identifier.identifier_value,
            identifier.server_id::text AS server_id,
            identifier.row_version
        FROM asset.external_identifier identifier
        WHERE identifier.source_system = %s
          AND identifier.identifier_type = %s
          AND identifier.server_id IS NOT NULL
          AND identifier.valid_to IS NULL
        ORDER BY identifier.external_identifier_id
        """,
        (SERVER_SOURCE_SYSTEM, SERVER_IDENTIFIER_TYPE),
    )
    gateway_identifiers = _rows(
        connection,
        """
        SELECT
            identifier.external_identifier_id::text AS external_identifier_id,
            identifier.source_system,
            identifier.identifier_type,
            identifier.identifier_value,
            identifier.runtime_id::text AS runtime_id,
            identifier.row_version
        FROM asset.external_identifier identifier
        WHERE identifier.identifier_type IN ('gateway_id', 'legacy_runtime_id')
          AND identifier.runtime_id IS NOT NULL
          AND identifier.valid_to IS NULL
        ORDER BY identifier.external_identifier_id
        """,
    )
    identifier_occupancy = _rows(
        connection,
        """
        SELECT
            identifier.external_identifier_id::text AS external_identifier_id,
            identifier.source_system,
            identifier.identifier_type,
            identifier.identifier_value,
            CASE
                WHEN identifier.environment_id IS NOT NULL THEN 'environment'
                WHEN identifier.cloud_provider_id IS NOT NULL THEN 'cloud_provider'
                WHEN identifier.server_id IS NOT NULL THEN 'server'
                WHEN identifier.group_id IS NOT NULL THEN 'group'
                WHEN identifier.runtime_id IS NOT NULL THEN 'runtime'
                WHEN identifier.agent_id IS NOT NULL THEN 'agent'
                WHEN identifier.department_id IS NOT NULL THEN 'department'
                WHEN identifier.person_id IS NOT NULL THEN 'person'
            END AS target_type,
            COALESCE(
                identifier.environment_id,
                identifier.cloud_provider_id,
                identifier.server_id,
                identifier.group_id,
                identifier.runtime_id,
                identifier.agent_id,
                identifier.department_id,
                identifier.person_id
            )::text AS target_id,
            identifier.row_version,
            identifier.valid_to IS NULL AS is_current
        FROM asset.external_identifier identifier
        ORDER BY identifier.external_identifier_id
        """,
    )
    return normalize_snapshot(
        {
            "database": {
                "name": normalize_text(metadata["name"]),
                "server_version_num": normalize_text(metadata["server_version_num"]),
            },
            "migrations": migrations,
            "runtimes": runtimes,
            "server_identifiers": server_identifiers,
            "gateway_identifiers": gateway_identifiers,
            "identifier_occupancy": identifier_occupancy,
        }
    )


def _uuid_text(value: Any, *, field: str) -> str:
    try:
        return str(uuid.UUID(normalize_text(value)))
    except ValueError as exc:
        raise AdoptionRejected(f"database snapshot contains invalid {field}") from exc


def _positive_row_version(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise AdoptionRejected(f"database snapshot contains invalid {field}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AdoptionRejected(f"database snapshot contains invalid {field}") from exc
    if result < 1:
        raise AdoptionRejected(f"database snapshot contains invalid {field}")
    return result


def normalize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a real or test snapshot into a canonical fingerprint shape."""

    database = snapshot.get("database")
    if not isinstance(database, Mapping):
        raise AdoptionRejected("database snapshot metadata is missing")
    server_version_num = normalize_text(database.get("server_version_num"))
    if not re.fullmatch(r"[0-9]+", server_version_num):
        raise AdoptionRejected("database server_version_num is invalid")
    if int(server_version_num) < 160000:
        raise AdoptionRejected("PostgreSQL 16 or newer is required")
    normalized_migrations: list[dict[str, str]] = []
    seen_migration_versions: set[str] = set()
    for raw in snapshot.get("migrations", []):
        version = normalize_text(raw.get("version"))
        filename = normalize_text(raw.get("filename"))
        sha256 = normalize_text(raw.get("sha256"))
        if not re.fullmatch(r"[0-9]{3}", version) or version in seen_migration_versions:
            raise AdoptionRejected("database snapshot has an invalid migration ledger version")
        if not filename or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise AdoptionRejected("database snapshot has an invalid migration ledger row")
        seen_migration_versions.add(version)
        normalized_migrations.append(
            {"version": version, "filename": filename, "sha256": sha256}
        )
    if not normalized_migrations or min(seen_migration_versions) != "001":
        raise AdoptionRejected("database migration ledger does not start at version 001")
    normalized_runtimes: list[dict[str, Any]] = []
    seen_runtimes: set[str] = set()
    for raw in snapshot.get("runtimes", []):
        runtime_id = _uuid_text(raw.get("runtime_id"), field="runtime_id")
        if runtime_id in seen_runtimes:
            raise AdoptionRejected(f"database snapshot has duplicate runtime_id: {runtime_id}")
        seen_runtimes.add(runtime_id)
        host_value = raw.get("monitor_host")
        monitor_host = (
            _normalize_ip(host_value, field=f"runtime {runtime_id} monitor_host")
            if normalize_text(host_value)
            else None
        )
        port_value = raw.get("monitor_port")
        monitor_port = (
            _normalize_port(port_value, field=f"runtime {runtime_id} monitor_port")
            if port_value is not None
            else None
        )
        normalized_runtimes.append(
            {
                "runtime_id": runtime_id,
                "server_id": _uuid_text(raw.get("server_id"), field="runtime.server_id"),
                "family_code": normalize_text(raw.get("family_code")),
                "runtime_role": normalize_text(raw.get("runtime_role")),
                "monitor_host": monitor_host,
                "monitor_port": monitor_port,
                "runtime_row_version": _positive_row_version(
                    raw.get("runtime_row_version"), field="runtime.row_version"
                ),
                "server_row_version": _positive_row_version(
                    raw.get("server_row_version"), field="server.row_version"
                ),
            }
        )
    normalized_server_ids: list[dict[str, Any]] = []
    for raw in snapshot.get("server_identifiers", []):
        identifier_value = normalize_text(raw.get("identifier_value"))
        if not identifier_value:
            raise AdoptionRejected("database snapshot has an empty server inventory identifier")
        normalized_server_ids.append(
            {
                "external_identifier_id": _uuid_text(
                    raw.get("external_identifier_id"), field="external_identifier_id"
                ),
                "identifier_value": identifier_value,
                "server_id": _uuid_text(raw.get("server_id"), field="identifier.server_id"),
                "row_version": _positive_row_version(
                    raw.get("row_version"), field="identifier.row_version"
                ),
            }
        )
    normalized_gateway_ids: list[dict[str, Any]] = []
    for raw in snapshot.get("gateway_identifiers", []):
        identifier_type = normalize_text(raw.get("identifier_type"))
        if identifier_type not in {"gateway_id", "legacy_runtime_id"}:
            raise AdoptionRejected("database snapshot has an unexpected Gateway identifier type")
        identifier_value = normalize_text(raw.get("identifier_value"))
        if not identifier_value:
            raise AdoptionRejected("database snapshot has an empty Gateway identifier")
        normalized_gateway_ids.append(
            {
                "external_identifier_id": _uuid_text(
                    raw.get("external_identifier_id"), field="external_identifier_id"
                ),
                "source_system": normalize_text(raw.get("source_system")),
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "runtime_id": _uuid_text(raw.get("runtime_id"), field="identifier.runtime_id"),
                "row_version": _positive_row_version(
                    raw.get("row_version"), field="identifier.row_version"
                ),
            }
        )
    normalized_occupancy: list[dict[str, Any]] = []
    seen_occupied_ids: set[str] = set()
    for raw in snapshot.get("identifier_occupancy", []):
        identifier_id = _uuid_text(
            raw.get("external_identifier_id"), field="occupied external_identifier_id"
        )
        if identifier_id in seen_occupied_ids:
            raise AdoptionRejected(
                f"database snapshot has duplicate occupied identifier UUID: {identifier_id}"
            )
        seen_occupied_ids.add(identifier_id)
        source_system = normalize_text(raw.get("source_system"))
        identifier_type = normalize_text(raw.get("identifier_type"))
        identifier_value = normalize_text(raw.get("identifier_value"))
        target_type = normalize_text(raw.get("target_type"))
        if not source_system or not identifier_type or not identifier_value:
            raise AdoptionRejected("database snapshot has invalid identifier occupancy")
        if target_type not in {
            "environment",
            "cloud_provider",
            "server",
            "group",
            "runtime",
            "agent",
            "department",
            "person",
        }:
            raise AdoptionRejected("database snapshot has invalid occupied target type")
        if not isinstance(raw.get("is_current"), bool):
            raise AdoptionRejected("database snapshot has invalid occupied identifier validity")
        normalized_occupancy.append(
            {
                "external_identifier_id": identifier_id,
                "source_system": source_system,
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "target_type": target_type,
                "target_id": _uuid_text(
                    raw.get("target_id"), field="occupied identifier target_id"
                ),
                "row_version": _positive_row_version(
                    raw.get("row_version"), field="occupied identifier row_version"
                ),
                "is_current": raw["is_current"],
            }
        )
    return {
        "database": {
            "name": normalize_text(database.get("name")),
            "server_version_num": server_version_num,
        },
        "migrations": sorted(normalized_migrations, key=lambda row: row["version"]),
        "runtimes": sorted(normalized_runtimes, key=lambda row: row["runtime_id"]),
        "server_identifiers": sorted(
            normalized_server_ids, key=lambda row: row["external_identifier_id"]
        ),
        "gateway_identifiers": sorted(
            normalized_gateway_ids, key=lambda row: row["external_identifier_id"]
        ),
        "identifier_occupancy": sorted(
            normalized_occupancy, key=lambda row: row["external_identifier_id"]
        ),
    }


def _mapping_identity(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: mapping[key]
        for key in (
            "external_identifier_id",
            "gateway_id",
            "stable_server_id",
            "server_id",
            "runtime_id",
            "monitor_host",
            "monitor_port",
            "native_scrape_enabled",
        )
    }


def _database_fingerprint(snapshot: Mapping[str, Any], mappings: Iterable[Mapping[str, Any]]) -> str:
    """Fingerprint the pre-adoption baseline, excluding exact tool-owned rows.

    Excluding only deterministic, exact rows permits a confirmed plan to be
    replayed after its own successful transaction while still rejecting every
    other identity or asset drift.
    """

    exact = {
        (
            mapping["external_identifier_id"],
            SOURCE_SYSTEM,
            GATEWAY_IDENTIFIER_TYPE,
            mapping["gateway_id"],
            mapping["runtime_id"],
            1,
        )
        for mapping in mappings
    }
    baseline = {
        **snapshot,
        "gateway_identifiers": [
            row
            for row in snapshot["gateway_identifiers"]
            if (
                row["external_identifier_id"],
                row["source_system"],
                row["identifier_type"],
                row["identifier_value"],
                row["runtime_id"],
                row["row_version"],
            )
            not in exact
        ],
        "identifier_occupancy": [
            row
            for row in snapshot["identifier_occupancy"]
            if (
                row["external_identifier_id"],
                row["source_system"],
                row["identifier_type"],
                row["identifier_value"],
                row["target_id"],
                row["row_version"],
            )
            not in exact
            or row["target_type"] != "runtime"
            or not row["is_current"]
        ],
    }
    return canonical_hash(baseline)


def resolve_adoption(
    inventory: list[dict[str, Any]], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    snapshot = normalize_snapshot(snapshot)
    runtimes_by_server: dict[str, list[dict[str, Any]]] = {}
    runtime_by_id: dict[str, dict[str, Any]] = {}
    for runtime in snapshot["runtimes"]:
        runtime_by_id[runtime["runtime_id"]] = runtime
        runtimes_by_server.setdefault(runtime["server_id"], []).append(runtime)

    server_identifiers: dict[str, list[dict[str, Any]]] = {}
    for identifier in snapshot["server_identifiers"]:
        server_identifiers.setdefault(canonical_key(identifier["identifier_value"]), []).append(
            identifier
        )

    gateway_ids_by_key: dict[str, list[dict[str, Any]]] = {}
    gateway_ids_by_runtime: dict[str, list[dict[str, Any]]] = {}
    external_ids: dict[str, dict[str, Any]] = {
        row["external_identifier_id"]: row
        for row in snapshot["identifier_occupancy"]
    }
    for identifier in snapshot["gateway_identifiers"]:
        if identifier["identifier_type"] == GATEWAY_IDENTIFIER_TYPE:
            gateway_ids_by_key.setdefault(
                canonical_key(identifier["identifier_value"]), []
            ).append(identifier)
            gateway_ids_by_runtime.setdefault(identifier["runtime_id"], []).append(identifier)

    mappings: list[dict[str, Any]] = []
    mapped_runtime_ids: set[str] = set()
    overlay: dict[str, dict[str, Any]] = {}
    for item in inventory:
        matches = server_identifiers.get(item["stable_server_key"], [])
        if len(matches) != 1:
            raise AdoptionRejected(
                f"metadata.server_id {item['stable_server_id']!r} resolves to "
                f"{len(matches)} current {SERVER_SOURCE_SYSTEM}/{SERVER_IDENTIFIER_TYPE} rows"
            )
        server_identifier = matches[0]
        runtimes = runtimes_by_server.get(server_identifier["server_id"], [])
        if len(runtimes) != 1:
            raise AdoptionRejected(
                f"metadata.server_id {item['stable_server_id']!r} resolves to "
                f"{len(runtimes)} OpenClaw gateway runtimes"
            )
        runtime = runtimes[0]
        if runtime["family_code"] != "openclaw" or runtime["runtime_role"] != "gateway":
            raise AdoptionRejected("database snapshot contains a non-OpenClaw gateway candidate")
        if runtime["monitor_host"] is None or runtime["monitor_host"] != item["host"]:
            raise AdoptionRejected(
                f"host consistency check failed for {item['gateway_id']}: "
                f"inventory={item['host']}, database={runtime['monitor_host'] or '<missing>'}"
            )
        if runtime["monitor_port"] is None or runtime["monitor_port"] != item["port"]:
            raise AdoptionRejected(
                f"port consistency check failed for {item['gateway_id']}: "
                f"inventory={item['port']}, database={runtime['monitor_port'] or '<missing>'}"
            )
        runtime_id = runtime["runtime_id"]
        if runtime_id in mapped_runtime_ids:
            raise AdoptionRejected(f"multiple inventory rows resolve to runtime {runtime_id}")
        expected_external_id = external_identifier_id(item["gateway_id"])
        identifier_id_occupant = external_ids.get(expected_external_id)
        candidates = gateway_ids_by_key.get(item["gateway_key"], [])
        allowed_exact: dict[str, Any] | None = None
        if candidates:
            if len(candidates) != 1:
                raise AdoptionRejected(
                    f"gateway_id {item['gateway_id']!r} has multiple current database mappings"
                )
            candidate = candidates[0]
            exact = (
                candidate["external_identifier_id"] == expected_external_id
                and candidate["source_system"] == SOURCE_SYSTEM
                and candidate["identifier_type"] == GATEWAY_IDENTIFIER_TYPE
                and candidate["identifier_value"] == item["gateway_id"]
                and candidate["runtime_id"] == runtime_id
                and candidate["row_version"] == 1
            )
            if not exact:
                raise AdoptionRejected(
                    f"gateway_id {item['gateway_id']!r} conflicts with a current database mapping"
                )
            allowed_exact = candidate
        occupant_is_exact = (
            identifier_id_occupant is not None
            and identifier_id_occupant["source_system"] == SOURCE_SYSTEM
            and identifier_id_occupant["identifier_type"] == GATEWAY_IDENTIFIER_TYPE
            and identifier_id_occupant["identifier_value"] == item["gateway_id"]
            and identifier_id_occupant["target_type"] == "runtime"
            and identifier_id_occupant["target_id"] == runtime_id
            and identifier_id_occupant["row_version"] == 1
            and identifier_id_occupant["is_current"]
        )
        if identifier_id_occupant is not None and not occupant_is_exact:
            raise AdoptionRejected(
                f"deterministic external identifier UUID is already occupied for {item['gateway_id']}"
            )
        if (allowed_exact is not None) != occupant_is_exact:
            raise AdoptionRejected(
                f"database snapshot is inconsistent for exact Gateway identity {item['gateway_id']}"
            )
        other_runtime_ids = [
            identifier
            for identifier in gateway_ids_by_runtime.get(runtime_id, [])
            if identifier is not allowed_exact
        ]
        if other_runtime_ids:
            values = ", ".join(
                sorted(identifier["identifier_value"] for identifier in other_runtime_ids)
            )
            raise AdoptionRejected(
                f"runtime {runtime_id} already has conflicting gateway_id value(s): {values}"
            )
        mapping = {
            "external_identifier_id": expected_external_id,
            "gateway_id": item["gateway_id"],
            "stable_server_id": item["stable_server_id"],
            "server_id": runtime["server_id"],
            "runtime_id": runtime_id,
            "monitor_host": runtime["monitor_host"],
            "monitor_port": runtime["monitor_port"],
            "native_scrape_enabled": item["enabled"],
            "action": "reuse" if allowed_exact is not None else "insert",
        }
        mappings.append(mapping)
        mapped_runtime_ids.add(runtime_id)
        overlay[runtime_id] = {
            "native_scrape_enabled": item["enabled"],
            "scrape_host": item["host"],
            "scrape_port": item["port"],
        }

    unmatched_runtimes: list[dict[str, Any]] = []
    server_values: dict[str, list[str]] = {}
    for identifier in snapshot["server_identifiers"]:
        server_values.setdefault(identifier["server_id"], []).append(
            identifier["identifier_value"]
        )
    for runtime_id, runtime in sorted(runtime_by_id.items()):
        if runtime_id not in mapped_runtime_ids:
            unmatched_runtimes.append(
                {
                    "runtime_id": runtime_id,
                    "server_id": runtime["server_id"],
                    "stable_server_ids": sorted(server_values.get(runtime["server_id"], [])),
                    "reason": "not_present_in_legacy_inventory",
                }
            )
    mappings.sort(key=lambda mapping: mapping["runtime_id"])
    overlay = {runtime_id: overlay[runtime_id] for runtime_id in sorted(overlay)}
    return {
        "database": snapshot["database"],
        "database_fingerprint": _database_fingerprint(snapshot, mappings),
        "mappings": mappings,
        "unmatched_runtimes": unmatched_runtimes,
        "native_scrape_overlay": overlay,
    }


def build_plan(
    *,
    inventory_path: Path,
    inventory_sha256: str,
    inventory: list[dict[str, Any]],
    snapshot: Mapping[str, Any],
    observed_at: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_adoption(inventory, snapshot)
    timestamp = normalize_timestamp(
        observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    payload = {
        "plan_version": PLAN_VERSION,
        "observed_at": timestamp,
        "inventory": {
            "file_name": Path(inventory_path).name,
            "sha256": inventory_sha256,
            "gateway_count": len(inventory),
        },
        "identity_policy": {
            "primary_match": f"{SERVER_SOURCE_SYSTEM}/{SERVER_IDENTIFIER_TYPE}",
            "consistency_checks": ["host", "port"],
            "write_mode": "append_only",
            "source_system": SOURCE_SYSTEM,
            "identifier_type": GATEWAY_IDENTIFIER_TYPE,
        },
        "database": {
            **resolved["database"],
            "fingerprint_algorithm": "sha256-canonical-json-baseline-v1",
            "fingerprint": resolved["database_fingerprint"],
        },
        "summary": {
            "mapping_count": len(resolved["mappings"]),
            "insert_count": sum(
                mapping["action"] == "insert" for mapping in resolved["mappings"]
            ),
            "reuse_count": sum(
                mapping["action"] == "reuse" for mapping in resolved["mappings"]
            ),
            "unmatched_runtime_count": len(resolved["unmatched_runtimes"]),
        },
        "mappings": resolved["mappings"],
        "unmatched_runtimes": resolved["unmatched_runtimes"],
        "native_scrape_overlay": resolved["native_scrape_overlay"],
    }
    return {**payload, "plan_hash": canonical_hash(payload)}


def _atomic_write_json(path: Path, payload: Any) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise AdoptionRejected(f"refusing to overwrite symbolic link: {path}")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o640)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return file_sha256(path)


def _validate_plan_document(plan: Any) -> None:
    if not isinstance(plan, dict):
        raise AdoptionRejected("plan root must be an object")
    expected_plan_fields = {
        "plan_version",
        "observed_at",
        "inventory",
        "identity_policy",
        "database",
        "summary",
        "mappings",
        "unmatched_runtimes",
        "native_scrape_overlay",
        "plan_hash",
    }
    if set(plan) != expected_plan_fields:
        raise AdoptionRejected("plan fields differ from the adoption contract")
    if plan.get("plan_version") != PLAN_VERSION:
        raise AdoptionRejected(f"unsupported plan_version: {plan.get('plan_version')!r}")
    supplied_hash = normalize_text(plan.get("plan_hash"))
    if not re.fullmatch(r"[0-9a-f]{64}", supplied_hash):
        raise AdoptionRejected("plan_hash must be a lowercase SHA-256")
    payload = {key: value for key, value in plan.items() if key != "plan_hash"}
    if not hmac.compare_digest(supplied_hash, canonical_hash(payload)):
        raise AdoptionRejected("plan_hash does not match the canonical plan payload")
    if normalize_timestamp(plan.get("observed_at")) != plan["observed_at"]:
        raise AdoptionRejected("observed_at must use canonical UTC RFC 3339 form")
    inventory = plan.get("inventory")
    if not isinstance(inventory, dict) or set(inventory) != {
        "file_name",
        "sha256",
        "gateway_count",
    }:
        raise AdoptionRejected("plan inventory fields differ from the contract")
    if (
        not normalize_text(inventory["file_name"])
        or Path(inventory["file_name"]).name != inventory["file_name"]
    ):
        raise AdoptionRejected("plan inventory file_name must be a basename")
    if not re.fullmatch(r"[0-9a-f]{64}", normalize_text(inventory["sha256"])):
        raise AdoptionRejected("plan inventory sha256 is invalid")
    if isinstance(inventory["gateway_count"], bool) or not isinstance(
        inventory["gateway_count"], int
    ) or inventory["gateway_count"] < 1:
        raise AdoptionRejected("plan inventory gateway_count is invalid")
    if plan.get("identity_policy") != {
        "primary_match": f"{SERVER_SOURCE_SYSTEM}/{SERVER_IDENTIFIER_TYPE}",
        "consistency_checks": ["host", "port"],
        "write_mode": "append_only",
        "source_system": SOURCE_SYSTEM,
        "identifier_type": GATEWAY_IDENTIFIER_TYPE,
    }:
        raise AdoptionRejected("plan identity policy differs from the fail-closed contract")
    database = plan.get("database")
    if not isinstance(database, dict) or set(database) != {
        "name",
        "server_version_num",
        "fingerprint_algorithm",
        "fingerprint",
    }:
        raise AdoptionRejected("plan database fields differ from the contract")
    if not normalize_text(database["name"]):
        raise AdoptionRejected("plan database name is empty")
    if not re.fullmatch(r"[0-9]+", normalize_text(database["server_version_num"])):
        raise AdoptionRejected("plan database server_version_num is invalid")
    if int(database["server_version_num"]) < 160000:
        raise AdoptionRejected("plan requires PostgreSQL 16 or newer")
    if database["fingerprint_algorithm"] != "sha256-canonical-json-baseline-v1":
        raise AdoptionRejected("plan database fingerprint algorithm is unsupported")
    if not re.fullmatch(r"[0-9a-f]{64}", normalize_text(database["fingerprint"])):
        raise AdoptionRejected("plan database fingerprint is invalid")
    if not isinstance(plan.get("mappings"), list) or not plan["mappings"]:
        raise AdoptionRejected("plan must contain at least one mapping")
    expected_mapping_fields = {
        "external_identifier_id",
        "gateway_id",
        "stable_server_id",
        "server_id",
        "runtime_id",
        "monitor_host",
        "monitor_port",
        "native_scrape_enabled",
        "action",
    }
    seen_gateway_ids: set[str] = set()
    seen_runtime_ids: set[str] = set()
    for mapping in plan["mappings"]:
        if not isinstance(mapping, dict) or set(mapping) != expected_mapping_fields:
            raise AdoptionRejected("plan mapping fields differ from the contract")
        _mapping_identity(mapping)
        gateway_id = normalize_text(mapping["gateway_id"])
        if not GATEWAY_ID_PATTERN.fullmatch(gateway_id):
            raise AdoptionRejected("plan mapping contains an invalid gateway_id")
        gateway_key = canonical_key(gateway_id)
        if gateway_key in seen_gateway_ids:
            raise AdoptionRejected("plan contains duplicate gateway_id mappings")
        seen_gateway_ids.add(gateway_key)
        runtime_id = _uuid_text(mapping["runtime_id"], field="plan runtime_id")
        _uuid_text(mapping["server_id"], field="plan server_id")
        identifier_id = _uuid_text(
            mapping["external_identifier_id"], field="plan external_identifier_id"
        )
        if identifier_id != external_identifier_id(gateway_id):
            raise AdoptionRejected("plan mapping external identifier UUID is not deterministic")
        if runtime_id in seen_runtime_ids:
            raise AdoptionRejected("plan contains duplicate runtime mappings")
        seen_runtime_ids.add(runtime_id)
        if not normalize_text(mapping["stable_server_id"]):
            raise AdoptionRejected("plan mapping stable_server_id is empty")
        _normalize_ip(mapping["monitor_host"], field="plan mapping monitor_host")
        _normalize_port(mapping["monitor_port"], field="plan mapping monitor_port")
        if not isinstance(mapping["native_scrape_enabled"], bool):
            raise AdoptionRejected("plan mapping native_scrape_enabled must be a boolean")
        if mapping["action"] not in {"insert", "reuse"}:
            raise AdoptionRejected("plan mapping action is invalid")
    if not isinstance(plan.get("native_scrape_overlay"), dict):
        raise AdoptionRejected("plan native_scrape_overlay must be an object")
    expected_overlay = {
        mapping["runtime_id"]: {
            "native_scrape_enabled": mapping["native_scrape_enabled"],
            "scrape_host": mapping["monitor_host"],
            "scrape_port": mapping["monitor_port"],
        }
        for mapping in plan["mappings"]
    }
    if plan["native_scrape_overlay"] != expected_overlay:
        raise AdoptionRejected("plan native scrape overlay differs from the resolved mappings")
    unmatched = plan.get("unmatched_runtimes")
    if not isinstance(unmatched, list):
        raise AdoptionRejected("plan unmatched_runtimes must be an array")
    unmatched_ids: set[str] = set()
    for runtime in unmatched:
        if not isinstance(runtime, dict) or set(runtime) != {
            "runtime_id",
            "server_id",
            "stable_server_ids",
            "reason",
        }:
            raise AdoptionRejected("unmatched Runtime fields differ from the contract")
        runtime_id = _uuid_text(runtime["runtime_id"], field="unmatched runtime_id")
        _uuid_text(runtime["server_id"], field="unmatched server_id")
        if runtime_id in seen_runtime_ids or runtime_id in unmatched_ids:
            raise AdoptionRejected("plan has duplicate or contradictory unmatched Runtime")
        unmatched_ids.add(runtime_id)
        if runtime["reason"] != "not_present_in_legacy_inventory":
            raise AdoptionRejected("plan has an unsupported unmatched Runtime reason")
        if not isinstance(runtime["stable_server_ids"], list) or not all(
            isinstance(value, str) and value.strip()
            for value in runtime["stable_server_ids"]
        ):
            raise AdoptionRejected("unmatched Runtime stable_server_ids are invalid")
    summary = plan.get("summary")
    expected_summary = {
        "mapping_count": len(plan["mappings"]),
        "insert_count": sum(row["action"] == "insert" for row in plan["mappings"]),
        "reuse_count": sum(row["action"] == "reuse" for row in plan["mappings"]),
        "unmatched_runtime_count": len(unmatched),
    }
    if summary != expected_summary:
        raise AdoptionRejected("plan summary differs from its detailed rows")


def load_confirmed_plan(path: Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    expected_sha256 = normalize_text(expected_sha256)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise AdoptionRejected("--expected-plan-sha256 must be a lowercase SHA-256")
    plan = _load_json(path, maximum_bytes=MAX_PLAN_BYTES, label="plan")
    actual_sha256 = file_sha256(path)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise AdoptionRejected(
            f"plan file SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    _validate_plan_document(plan)
    return plan, actual_sha256


def _semantic_mappings(mappings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (_mapping_identity(mapping) for mapping in mappings),
        key=lambda mapping: mapping["runtime_id"],
    )


def _validate_action_transitions(
    planned: Iterable[Mapping[str, Any]], current: Iterable[Mapping[str, Any]]
) -> None:
    planned_actions = {row["runtime_id"]: row["action"] for row in planned}
    current_actions = {row["runtime_id"]: row["action"] for row in current}
    for runtime_id, planned_action in planned_actions.items():
        current_action = current_actions[runtime_id]
        if planned_action == "reuse" and current_action != "reuse":
            raise AdoptionRejected(
                f"previously adopted Gateway identity disappeared for runtime {runtime_id}"
            )


def _receipt(connection: psycopg.Connection, identifier_id: str) -> Mapping[str, Any] | None:
    return connection.execute(
        """
        SELECT
            change_id::text AS change_id,
            actor_type,
            actor_ref,
            source,
            object_type,
            object_id::text AS object_id,
            operation::text AS operation,
            after_value
        FROM audit.change_log
        WHERE change_id = %s
        """,
        (change_id(identifier_id),),
    ).fetchone()


def validate_reuse_receipts(
    connection: psycopg.Connection, mappings: Iterable[Mapping[str, Any]]
) -> None:
    for mapping in mappings:
        if mapping.get("action") != "reuse":
            continue
        receipt = _receipt(connection, mapping["external_identifier_id"])
        expected_fields = {
            "source_system": SOURCE_SYSTEM,
            "identifier_type": GATEWAY_IDENTIFIER_TYPE,
            "identifier_value": mapping["gateway_id"],
            "runtime_id": mapping["runtime_id"],
        }
        after_value = receipt.get("after_value") if receipt else None
        valid = (
            receipt is not None
            and receipt["change_id"] == change_id(mapping["external_identifier_id"])
            and receipt["actor_type"] == "migration"
            and bool(normalize_text(receipt["actor_ref"]))
            and receipt["source"] == "migration"
            and receipt["object_type"] == "external_identifier"
            and receipt["object_id"] == mapping["external_identifier_id"]
            and receipt["operation"] == "link"
            and isinstance(after_value, dict)
            and all(after_value.get(key) == value for key, value in expected_fields.items())
        )
        if not valid:
            raise AdoptionRejected(
                f"exact identifier {mapping['external_identifier_id']} has no valid immutable adoption receipt"
            )


def _insert_mapping(
    connection: psycopg.Connection,
    *,
    mapping: Mapping[str, Any],
    observed_at: str,
    actor_ref: str,
    correlation_id: str,
) -> None:
    after_value = {
        "external_identifier_id": mapping["external_identifier_id"],
        "source_system": SOURCE_SYSTEM,
        "identifier_type": GATEWAY_IDENTIFIER_TYPE,
        "identifier_value": mapping["gateway_id"],
        "runtime_id": mapping["runtime_id"],
        "valid_from": observed_at,
    }
    connection.execute(
        """
        INSERT INTO asset.external_identifier (
            external_identifier_id, source_system, identifier_type,
            identifier_value, runtime_id, valid_from
        ) VALUES (%s, %s, %s, %s, %s, %s::timestamptz)
        """,
        (
            mapping["external_identifier_id"],
            SOURCE_SYSTEM,
            GATEWAY_IDENTIFIER_TYPE,
            mapping["gateway_id"],
            mapping["runtime_id"],
            observed_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO audit.change_log (
            change_id, actor_type, actor_ref, source, object_type, object_id,
            operation, changed_fields, before_value, after_value,
            reason, correlation_id
        ) VALUES (
            %s, 'migration', %s, 'migration', 'external_identifier', %s,
            'link', %s, NULL, %s,
            %s, %s
        )
        """,
        (
            change_id(mapping["external_identifier_id"]),
            actor_ref,
            mapping["external_identifier_id"],
            [
                "source_system",
                "identifier_type",
                "identifier_value",
                "runtime_id",
                "valid_from",
            ],
            Jsonb(after_value),
            "Append-only adoption of a legacy observability Gateway identity",
            correlation_id,
        ),
    )


def _postcondition(connection: psycopg.Connection, mappings: list[Mapping[str, Any]]) -> None:
    for mapping in mappings:
        row = connection.execute(
            """
            SELECT
                external_identifier_id::text AS external_identifier_id,
                source_system,
                identifier_type,
                identifier_value,
                runtime_id::text AS runtime_id,
                row_version
            FROM asset.external_identifier
            WHERE source_system = %s
              AND identifier_type = %s
              AND identifier_value = %s
              AND valid_to IS NULL
            """,
            (SOURCE_SYSTEM, GATEWAY_IDENTIFIER_TYPE, mapping["gateway_id"]),
        ).fetchone()
        expected = {
            "external_identifier_id": mapping["external_identifier_id"],
            "source_system": SOURCE_SYSTEM,
            "identifier_type": GATEWAY_IDENTIFIER_TYPE,
            "identifier_value": mapping["gateway_id"],
            "runtime_id": mapping["runtime_id"],
            "row_version": 1,
        }
        if row is None or dict(row) != expected:
            raise AdoptionRejected(
                f"postcondition failed for Gateway identity {mapping['gateway_id']}"
            )
        if _receipt(connection, mapping["external_identifier_id"]) is None:
            raise AdoptionRejected(
                f"postcondition failed: missing audit receipt for {mapping['gateway_id']}"
            )


def commit_plan(
    *,
    plan_path: Path,
    expected_plan_sha256: str,
    inventory_path: Path,
    actor_ref: str,
) -> dict[str, Any]:
    actor_ref = normalize_text(actor_ref)
    if not actor_ref or len(actor_ref) > 200:
        raise AdoptionRejected("--actor-ref must contain 1 to 200 non-whitespace characters")
    plan, actual_plan_sha256 = load_confirmed_plan(plan_path, expected_plan_sha256)
    inventory, inventory_sha256 = load_inventory(inventory_path)
    if not hmac.compare_digest(inventory_sha256, plan["inventory"]["sha256"]):
        raise AdoptionRejected("inventory SHA-256 no longer matches the confirmed plan")
    if len(inventory) != plan["inventory"]["gateway_count"]:
        raise AdoptionRejected("inventory gateway count no longer matches the confirmed plan")

    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ WRITE")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOCK_NAME,)
        )
        snapshot = fetch_database_snapshot(connection)
        resolved = resolve_adoption(inventory, snapshot)
        if plan["database"]["name"] != resolved["database"]["name"]:
            raise AdoptionRejected("confirmed plan targets a different database")
        if not hmac.compare_digest(
            normalize_text(plan["database"]["fingerprint"]),
            resolved["database_fingerprint"],
        ):
            raise AdoptionRejected("database identity snapshot drifted after planning")
        if _semantic_mappings(plan["mappings"]) != _semantic_mappings(resolved["mappings"]):
            raise AdoptionRejected("resolved Gateway mappings drifted after planning")
        _validate_action_transitions(plan["mappings"], resolved["mappings"])
        if plan["unmatched_runtimes"] != resolved["unmatched_runtimes"]:
            raise AdoptionRejected("unmatched Runtime set drifted after planning")
        if plan["native_scrape_overlay"] != resolved["native_scrape_overlay"]:
            raise AdoptionRejected("native scrape overlay drifted after planning")
        validate_reuse_receipts(connection, resolved["mappings"])

        inserted = 0
        reused = 0
        correlation_id = stable_id("correlation", plan["plan_hash"])
        for mapping in resolved["mappings"]:
            if mapping["action"] == "reuse":
                reused += 1
                continue
            _insert_mapping(
                connection,
                mapping=mapping,
                observed_at=plan["observed_at"],
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
            inserted += 1
        _postcondition(connection, resolved["mappings"])
        return {
            "ok": True,
            "idempotent": inserted == 0,
            "plan_sha256": actual_plan_sha256,
            "plan_hash": plan["plan_hash"],
            "database_fingerprint": resolved["database_fingerprint"],
            "inserted": inserted,
            "reused": reused,
            "unmatched_runtimes": len(resolved["unmatched_runtimes"]),
        }


def plan_command(args: argparse.Namespace) -> dict[str, Any]:
    inventory_target = args.inventory.resolve(strict=False)
    output_target = args.output.resolve(strict=False)
    overlay_target = (
        args.overlay_output.resolve(strict=False) if args.overlay_output else None
    )
    if output_target == inventory_target:
        raise AdoptionRejected("plan output must not overwrite the source inventory")
    if overlay_target is not None and overlay_target in {inventory_target, output_target}:
        raise AdoptionRejected(
            "overlay output must differ from the source inventory and plan output"
        )
    inventory, inventory_sha256 = load_inventory(args.inventory)
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOCK_NAME,)
        )
        snapshot = fetch_database_snapshot(connection)
        plan = build_plan(
            inventory_path=args.inventory,
            inventory_sha256=inventory_sha256,
            inventory=inventory,
            snapshot=snapshot,
        )
        validate_reuse_receipts(connection, plan["mappings"])
    plan_file_sha256 = _atomic_write_json(args.output, plan)
    overlay_file_sha256 = None
    if args.overlay_output:
        # This is directly consumable by agentswatch_observability.control:
        # runtime_id is the top-level key and every value uses only safe fields.
        overlay_file_sha256 = _atomic_write_json(
            args.overlay_output, plan["native_scrape_overlay"]
        )
    return {
        "ok": True,
        "plan_file": str(args.output),
        "plan_sha256": plan_file_sha256,
        "plan_hash": plan["plan_hash"],
        "overlay_file": str(args.overlay_output) if args.overlay_output else None,
        "overlay_sha256": overlay_file_sha256,
        **plan["summary"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="build a read-only adoption plan")
    plan_parser.add_argument(
        "--inventory", type=Path, default=Path("cluster/lobsters.json")
    )
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--overlay-output", type=Path)
    plan_parser.set_defaults(handler=plan_command)

    commit_parser = commands.add_parser(
        "commit", help="append the exactly confirmed Gateway identifiers"
    )
    commit_parser.add_argument(
        "--inventory", type=Path, default=Path("cluster/lobsters.json")
    )
    commit_parser.add_argument("--plan-file", type=Path, required=True)
    commit_parser.add_argument("--expected-plan-sha256", required=True)
    commit_parser.add_argument("--actor-ref", required=True)
    commit_parser.set_defaults(
        handler=lambda args: commit_plan(
            plan_path=args.plan_file,
            expected_plan_sha256=args.expected_plan_sha256,
            inventory_path=args.inventory,
            actor_ref=args.actor_ref,
        )
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (AdoptionRejected, OSError, psycopg.Error, ValueError, KeyError) as exc:
        print(f"Gateway identity adoption rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
