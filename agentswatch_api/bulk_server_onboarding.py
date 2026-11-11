"""Safely plan and apply password-to-public-key onboarding for many servers.

The plan is deliberately secret-free and deterministic.  Applying a plan
requires the SHA-256 of its exact bytes, an explicit acceptance of the scanned
host keys, a fresh scan that matches the plan, and an interactively confirmed
password.  Connection and credential writes always go through
``ServerManagementService``; bootstrap runs always contain exactly one server.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from uuid import UUID

from agentswatch_automation.redaction import redact_sensitive_text

from .automation_library import ManagedOperationCatalog
from .database import Database
from .server_credentials import (
    ServerCredentialCipher,
    public_key_from_private_file,
    scan_ssh_host_keys,
    validate_ssh_connection_host,
    validate_ssh_host_keys,
)
from .server_management import OperationService, ServerManagementService


PLAN_KIND = "agentswatch-bulk-server-onboarding"
PLAN_SCHEMA_VERSION = 1
DEFAULT_WORKERS = 4
MAX_WORKERS = 16
MAX_PLAN_BYTES = 16 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.:\-]+$")
LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}
FORBIDDEN_PLAN_KEYS = {
    "password",
    "ssh_password",
    "become_password",
    "encrypted_password",
    "private_key",
    "private_key_file",
}
CLASSIFICATIONS = {
    "missing_internal_ip",
    "invalid_internal_ip",
    "loopback_internal_ip",
    "duplicate_internal_ip",
    "loopback_management_address",
    "unsafe_management_address",
    "existing_different_management_address",
    "already_completed",
    "needs_onboarding",
}
ACTIONABLE_CLASSIFICATIONS = {
    "loopback_management_address",
    "unsafe_management_address",
    "needs_onboarding",
}
PLAN_SERVER_FIELDS = {
    "server_id",
    "name",
    "server_row_version",
    "connection_row_version",
    "internal_ip",
    "existing_management_host",
    "existing_management_port",
    "classification",
    "action",
    "reason",
    "scan",
}
SKIP_REASON_BY_CLASSIFICATION = {
    "missing_internal_ip": "missing_internal_ip",
    "invalid_internal_ip": "invalid_internal_ip",
    "loopback_internal_ip": "loopback_internal_ip",
    "duplicate_internal_ip": "duplicate_internal_ip_is_ambiguous",
    "existing_different_management_address": "preserve_existing_different_management_address",
    "already_completed": "already_completed",
}


class BulkOnboardingRejected(RuntimeError):
    """Raised when a plan or apply request fails closed."""


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_worker_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_WORKERS:
        raise ValueError(f"worker count must be between 1 and {MAX_WORKERS}")
    return value


def parse_ip(value: Any):
    text = normalize_text(value)
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def is_loopback_host(value: Any) -> bool:
    text = normalize_text(value).lower().rstrip(".")
    if not text:
        return False
    if (
        text in LOOPBACK_NAMES
        or text.startswith("localhost.")
        or text.endswith(".localhost")
    ):
        return True
    parsed = parse_ip(text)
    return bool(parsed and parsed.is_loopback)


def is_usable_management_host(value: Any) -> bool:
    text = normalize_text(value)
    if (
        not text
        or len(text) > 500
        or text.startswith("-")
        or not HOST_PATTERN.fullmatch(text)
        or is_loopback_host(text)
    ):
        return False
    try:
        validate_ssh_connection_host(text, allow_loopback=False)
    except ValueError:
        return False
    return True


def hosts_equal(first: Any, second: Any) -> bool:
    first_text = normalize_text(first)
    second_text = normalize_text(second)
    first_ip = parse_ip(first_text)
    second_ip = parse_ip(second_text)
    if first_ip is not None and second_ip is not None:
        return first_ip == second_ip
    return first_text.casefold().rstrip(".") == second_text.casefold().rstrip(".")


def _normalized_settings(
    *, username: str, port: int, credential_ref: str, use_become: bool = False,
) -> dict[str, Any]:
    username = normalize_text(username)
    credential_ref = normalize_text(credential_ref)
    if not username or len(username) > 200:
        raise ValueError("username must be a non-empty value of at most 200 characters")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not credential_ref or len(credential_ref) > 300 or credential_ref == "ssh-agent":
        raise ValueError("credential_ref must name a platform private-key file")
    if not isinstance(use_become, bool):
        raise ValueError("use_become must be boolean")
    return {
        "username": username,
        "port": port,
        "credential_ref": credential_ref,
        "use_become": use_become,
    }


def _connection_is_clearly_completed(
    row: Mapping[str, Any], internal_ip: str, settings: Mapping[str, Any],
) -> bool:
    return bool(
        normalize_text(row.get("connection_type")) == "ssh"
        and hosts_equal(row.get("connection_host"), internal_ip)
        and row.get("connection_port") == settings["port"]
        and normalize_text(row.get("connection_username")) == settings["username"]
        and normalize_text(row.get("credential_ref")) == settings["credential_ref"]
        and bool(row.get("use_become")) == settings["use_become"]
        and bool(row.get("password_configured"))
        and normalize_text(row.get("bootstrap_status")) == "succeeded"
        and row.get("bootstrap_completed_at") is not None
        and normalize_text(row.get("connection_status")) == "reachable"
        and bool(row.get("ssh_host_keys"))
    )


def classify_server(
    row: Mapping[str, Any], settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the deterministic, secret-free planning row for one server."""

    server_id = normalize_text(row.get("server_id"))
    try:
        UUID(server_id)
    except ValueError as exc:
        raise ValueError(f"server_id is not a UUID: {server_id!r}") from exc
    internal_ip = normalize_text(row.get("internal_ip"))
    existing_host = normalize_text(row.get("connection_host")) or None
    base = {
        "server_id": server_id,
        "name": normalize_text(row.get("name")) or server_id,
        "server_row_version": row.get("server_row_version"),
        "connection_row_version": row.get("connection_row_version"),
        "internal_ip": internal_ip or None,
        "existing_management_host": existing_host,
        "existing_management_port": row.get("connection_port"),
        "action": "skip",
        "scan": None,
    }
    if not internal_ip:
        return {**base, "classification": "missing_internal_ip", "reason": "missing_internal_ip"}
    parsed = parse_ip(internal_ip)
    if parsed is None or parsed.is_unspecified or parsed.is_multicast:
        return {**base, "classification": "invalid_internal_ip", "reason": "invalid_internal_ip"}
    if parsed.is_loopback:
        return {**base, "classification": "loopback_internal_ip", "reason": "loopback_internal_ip"}
    if existing_host and is_loopback_host(existing_host):
        return {
            **base,
            "classification": "loopback_management_address",
            "reason": "replace_loopback_management_address",
            "action": "onboard",
        }
    if existing_host and not is_usable_management_host(existing_host):
        return {
            **base,
            "classification": "unsafe_management_address",
            "reason": "replace_unsafe_management_address",
            "action": "onboard",
        }
    if (
        existing_host
        and is_usable_management_host(existing_host)
        and (
            not hosts_equal(existing_host, internal_ip)
            or row.get("connection_port") not in {None, settings["port"]}
        )
    ):
        return {
            **base,
            "classification": "existing_different_management_address",
            "reason": "preserve_existing_different_management_address",
        }
    if _connection_is_clearly_completed(row, internal_ip, settings):
        return {**base, "classification": "already_completed", "reason": "already_completed"}
    return {
        **base,
        "classification": "needs_onboarding",
        "reason": "requires_password_bootstrap_and_public_key_verification",
        "action": "onboard",
    }


def normalize_scan_result(host: str, port: int, raw: Mapping[str, Any]) -> dict[str, list[str]]:
    raw_keys = raw.get("host_keys") if isinstance(raw, Mapping) else None
    if not isinstance(raw_keys, list):
        raise ValueError("SSH host key scan did not return host_keys")
    keys, fingerprints = validate_ssh_host_keys(host, port, raw_keys)
    pairs = sorted(zip(keys, fingerprints), key=lambda item: item[0])
    return {
        "host_keys": [key for key, _ in pairs],
        "fingerprints": [fingerprint for _, fingerprint in pairs],
    }


def scan_plan_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    port: int,
    max_workers: int = DEFAULT_WORKERS,
    scanner: Callable[[str, int], Mapping[str, Any]] = scan_ssh_host_keys,
) -> list[dict[str, Any]]:
    """Scan actionable rows concurrently while returning deterministic ordering."""

    max_workers = validate_worker_count(max_workers)
    copied = [dict(entry) for entry in entries]
    actionable = [entry for entry in copied if entry["action"] == "onboard"]
    results: dict[str, dict[str, list[str]] | None] = {}
    if actionable:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(actionable))) as executor:
            futures = {
                executor.submit(scanner, entry["internal_ip"], port): entry
                for entry in actionable
            }
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    results[entry["server_id"]] = normalize_scan_result(
                        entry["internal_ip"], port, future.result(),
                    )
                except Exception:
                    # Scanner diagnostics vary by OpenSSH and may contain network
                    # details.  Keep the signed plan deterministic and minimal.
                    results[entry["server_id"]] = None
    normalized = []
    for entry in copied:
        if entry["action"] != "onboard":
            normalized.append(entry)
            continue
        scan = results[entry["server_id"]]
        if scan is None:
            normalized.append({
                **entry,
                "action": "skip",
                "reason": "host_key_scan_failed",
                "scan": None,
            })
        else:
            normalized.append({**entry, "scan": scan})
    return sorted(normalized, key=lambda entry: entry["server_id"])


def mark_duplicate_management_targets(
    entries: Iterable[Mapping[str, Any]],
    *,
    duplicate_internal_ips: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Skip ambiguous internal IPs that could make two assets manage one host."""

    copied = [dict(entry) for entry in entries]
    if duplicate_internal_ips is None:
        duplicate_internal_ips = find_duplicate_internal_ips(copied)
    return [
        {
            **entry,
            "classification": "duplicate_internal_ip",
            "action": "skip",
            "reason": "duplicate_internal_ip_is_ambiguous",
            "scan": None,
        }
        if (
            parse_ip(entry.get("internal_ip")) is not None
            and str(parse_ip(entry["internal_ip"])) in duplicate_internal_ips
        )
        else entry
        for entry in copied
    ]


def find_duplicate_internal_ips(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    counts = Counter(
        str(parsed)
        for row in rows
        if (parsed := parse_ip(row.get("internal_ip"))) is not None
    )
    return {address for address, count in counts.items() if count > 1}


def select_requested_servers(
    rows: Iterable[Mapping[str, Any]], requested_server_ids: Iterable[Any] | None,
) -> list[dict[str, Any]]:
    """Select explicit active UUIDs and reject every missing or inactive request."""

    rows = [dict(row) for row in rows]
    requested_values = list(requested_server_ids or [])
    if not requested_values:
        return rows
    requested = set()
    for value in requested_values:
        try:
            requested.add(str(UUID(normalize_text(value))))
        except ValueError as exc:
            raise BulkOnboardingRejected(f"invalid requested server UUID: {value!r}") from exc
    active_by_id = {normalize_text(row.get("server_id")): row for row in rows}
    unavailable = sorted(requested - set(active_by_id))
    if unavailable:
        raise BulkOnboardingRejected(
            "requested servers are missing, disabled, or retired: " + ", ".join(unavailable)
        )
    return [active_by_id[server_id] for server_id in sorted(requested)]


def summarize_entries(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    entries = list(entries)
    classifications = Counter(entry["classification"] for entry in entries)
    actions = Counter(entry["action"] for entry in entries)
    return {
        "total": len(entries),
        "actions": {name: actions.get(name, 0) for name in ("onboard", "skip")},
        "classifications": {
            name: classifications.get(name, 0) for name in sorted(CLASSIFICATIONS)
        },
        "scan_failed": sum(entry.get("reason") == "host_key_scan_failed" for entry in entries),
    }


def build_plan(
    rows: Iterable[Mapping[str, Any]],
    *,
    username: str,
    port: int,
    credential_ref: str,
    use_become: bool = False,
    scan_workers: int = DEFAULT_WORKERS,
    scanner: Callable[[str, int], Mapping[str, Any]] = scan_ssh_host_keys,
    duplicate_check_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = _normalized_settings(
        username=username,
        port=port,
        credential_ref=credential_ref,
        use_become=use_become,
    )
    rows = list(rows)
    duplicate_source = rows if duplicate_check_rows is None else list(duplicate_check_rows)
    classified = mark_duplicate_management_targets(
        (classify_server(row, settings) for row in rows),
        duplicate_internal_ips=find_duplicate_internal_ips(duplicate_source),
    )
    server_ids = [entry["server_id"] for entry in classified]
    if len(server_ids) != len(set(server_ids)):
        raise BulkOnboardingRejected("database snapshot contains duplicate server IDs")
    entries = scan_plan_entries(
        classified, port=port, max_workers=scan_workers, scanner=scanner,
    )
    plan = {
        "kind": PLAN_KIND,
        "schema_version": PLAN_SCHEMA_VERSION,
        "settings": settings,
        "summary": summarize_entries(entries),
        "servers": entries,
    }
    validate_plan_document(plan)
    return plan


def _assert_no_secret_fields(value: Any, path: str = "plan") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_PLAN_KEYS:
                raise BulkOnboardingRejected(f"{path} contains forbidden secret field {key!r}")
            _assert_no_secret_fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_secret_fields(item, f"{path}[{index}]")


def validate_plan_document(plan: Mapping[str, Any]) -> None:
    if not isinstance(plan, Mapping):
        raise BulkOnboardingRejected("plan must be a JSON object")
    _assert_no_secret_fields(plan)
    if plan.get("kind") != PLAN_KIND or plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise BulkOnboardingRejected("unsupported bulk onboarding plan")
    settings = plan.get("settings")
    if not isinstance(settings, Mapping):
        raise BulkOnboardingRejected("plan.settings must be an object")
    try:
        normalized_settings = _normalized_settings(
            username=settings.get("username"),
            port=settings.get("port"),
            credential_ref=settings.get("credential_ref"),
            use_become=settings.get("use_become"),
        )
    except ValueError as exc:
        raise BulkOnboardingRejected(str(exc)) from exc
    if dict(settings) != normalized_settings:
        raise BulkOnboardingRejected("plan.settings contains unsupported or non-normalized values")
    servers = plan.get("servers")
    if not isinstance(servers, list):
        raise BulkOnboardingRejected("plan.servers must be an array")
    if servers != sorted(servers, key=lambda entry: normalize_text(entry.get("server_id"))):
        raise BulkOnboardingRejected("plan.servers must be sorted by server_id")
    seen = set()
    for entry in servers:
        if not isinstance(entry, Mapping):
            raise BulkOnboardingRejected("each plan server must be an object")
        if set(entry) != PLAN_SERVER_FIELDS:
            raise BulkOnboardingRejected("plan server fields differ from the supported schema")
        server_id = normalize_text(entry.get("server_id"))
        try:
            UUID(server_id)
        except ValueError as exc:
            raise BulkOnboardingRejected(f"invalid server UUID in plan: {server_id!r}") from exc
        if server_id in seen:
            raise BulkOnboardingRejected(f"duplicate server in plan: {server_id}")
        seen.add(server_id)
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise BulkOnboardingRejected(f"invalid server name in plan: {server_id}")
        server_version = entry.get("server_row_version")
        connection_version = entry.get("connection_row_version")
        existing_port = entry.get("existing_management_port")
        if isinstance(server_version, bool) or not isinstance(server_version, int) or server_version < 1:
            raise BulkOnboardingRejected(f"invalid server row version in plan: {server_id}")
        if connection_version is not None and (
            isinstance(connection_version, bool)
            or not isinstance(connection_version, int)
            or connection_version < 1
        ):
            raise BulkOnboardingRejected(f"invalid connection row version in plan: {server_id}")
        if existing_port is not None and (
            isinstance(existing_port, bool)
            or not isinstance(existing_port, int)
            or not 1 <= existing_port <= 65535
        ):
            raise BulkOnboardingRejected(f"invalid existing management port in plan: {server_id}")
        classification = entry.get("classification")
        if classification not in CLASSIFICATIONS:
            raise BulkOnboardingRejected(f"unsupported server classification: {classification!r}")
        action = entry.get("action")
        if action not in {"onboard", "skip"}:
            raise BulkOnboardingRejected(f"unsupported server action: {action!r}")
        if action == "onboard":
            host = normalize_text(entry.get("internal_ip"))
            parsed_host = parse_ip(host)
            if (
                classification not in ACTIONABLE_CLASSIFICATIONS
                or parsed_host is None
                or parsed_host.is_loopback
                or parsed_host.is_unspecified
                or parsed_host.is_multicast
            ):
                raise BulkOnboardingRejected(f"unsafe onboarding target: {server_id}")
            expected_reason = (
                "replace_loopback_management_address"
                if classification == "loopback_management_address"
                else "replace_unsafe_management_address"
                if classification == "unsafe_management_address"
                else "requires_password_bootstrap_and_public_key_verification"
            )
            if entry.get("reason") != expected_reason:
                raise BulkOnboardingRejected(f"invalid onboarding reason: {server_id}")
            scan = entry.get("scan")
            if not isinstance(scan, Mapping):
                raise BulkOnboardingRejected(f"onboarding target has no host key scan: {server_id}")
            try:
                normalized_scan = normalize_scan_result(host, settings["port"], scan)
            except ValueError as exc:
                raise BulkOnboardingRejected(f"invalid host key scan for {server_id}: {exc}") from exc
            if dict(scan) != normalized_scan:
                raise BulkOnboardingRejected(f"host key scan is not canonical for {server_id}")
        elif entry.get("scan") is not None:
            raise BulkOnboardingRejected(f"skipped target unexpectedly contains host keys: {server_id}")
        elif classification in ACTIONABLE_CLASSIFICATIONS:
            if entry.get("reason") != "host_key_scan_failed":
                raise BulkOnboardingRejected(f"invalid scan failure reason: {server_id}")
        elif entry.get("reason") != SKIP_REASON_BY_CLASSIFICATION[classification]:
            raise BulkOnboardingRejected(f"invalid skip reason: {server_id}")
    expected_summary = summarize_entries(servers)
    if plan.get("summary") != expected_summary:
        raise BulkOnboardingRejected("plan summary differs from its server rows")


def atomic_write_plan(path: Path, plan: Mapping[str, Any]) -> str:
    validate_plan_document(plan)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise BulkOnboardingRejected(f"refusing to overwrite symbolic link: {path}")
    if path.exists() and not path.is_file():
        raise BulkOnboardingRejected(f"plan output is not a regular file: {path}")
    payload = canonical_json_bytes(plan)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        path.chmod(0o600)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some filesystems do not support directory fsync.  The file itself
            # is already fully written and permissioned before replacement.
            pass
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return sha256_bytes(payload)


def load_confirmed_plan(path: Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    expected_sha256 = normalize_text(expected_sha256)
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise BulkOnboardingRejected("--plan-sha256 must be a lowercase SHA-256")
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BulkOnboardingRejected("plan must be a regular non-symbolic file")
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise BulkOnboardingRejected("plan file exceeds the size limit")
    if path.stat().st_mode & 0o077:
        raise BulkOnboardingRejected("plan file must not be accessible by group or other users")
    payload = path.read_bytes()
    actual_sha256 = sha256_bytes(payload)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise BulkOnboardingRejected(
            f"plan SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    try:
        plan = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BulkOnboardingRejected("plan is not valid JSON") from exc
    if payload != canonical_json_bytes(plan):
        raise BulkOnboardingRejected("plan bytes are not in the canonical JSON format")
    validate_plan_document(plan)
    return plan, actual_sha256


SERVER_SNAPSHOT_SQL = """
    SELECT s.server_id::text AS server_id, s.name,
           host(s.internal_ip) AS internal_ip, s.row_version AS server_row_version,
           c.row_version AS connection_row_version,
           c.connection_type::text AS connection_type,
           c.host AS connection_host, c.port AS connection_port,
           c.username AS connection_username, c.credential_ref,
           c.use_become, c.password_configured, c.ssh_host_keys,
           c.bootstrap_status::text AS bootstrap_status,
           c.bootstrap_completed_at,
           c.connection_status::text AS connection_status
    FROM asset.server_asset s
    LEFT JOIN asset.server_connection c ON c.server_id = s.server_id
    WHERE s.lifecycle_status NOT IN ('disabled', 'retired')
    ORDER BY s.server_id
"""

ACTIVE_TARGET_RUN_SQL = """
    SELECT host.server_id::text AS server_id, run.run_id::text AS run_id,
           run.operation_code
    FROM automation.operation_host_result host
    JOIN automation.operation_run run ON run.run_id = host.run_id
    WHERE host.server_id = ANY(%(server_ids)s)
      AND run.status IN ('queued', 'running')
    ORDER BY run.created_at, host.server_id
"""


def fetch_server_snapshot(database: Database | None = None) -> list[dict[str, Any]]:
    database = database or Database()
    return [dict(row) for row in database.fetch_all(SERVER_SNAPSHOT_SQL)]


@dataclass(frozen=True)
class PlatformPreflightContext:
    catalog: ManagedOperationCatalog
    database: Database

    def assert_no_active_runs(self, entries: Iterable[Mapping[str, Any]]) -> None:
        server_ids = [UUID(entry["server_id"]) for entry in entries]
        if not server_ids:
            return
        active = self.database.fetch_all(
            ACTIVE_TARGET_RUN_SQL, {"server_ids": server_ids},
        )
        if active:
            targets = ", ".join(
                sorted({normalize_text(row["server_id"]) for row in active})
            )
            raise BulkOnboardingRejected(
                "onboarding targets already have queued or running operations: " + targets
            )


def verify_platform_preconditions(
    plan: Mapping[str, Any], entries: Iterable[Mapping[str, Any]],
    *, database: Database | None = None,
) -> PlatformPreflightContext | None:
    """Validate the management runtime before reading or persisting a password."""

    entries = list(entries)
    if not entries:
        return None
    missing = [name for name in ("sshpass", "ansible-playbook") if shutil.which(name) is None]
    if missing:
        raise BulkOnboardingRejected(
            "management runtime is missing executables: " + ", ".join(missing)
        )
    if importlib.util.find_spec("ansible_runner") is None:
        raise BulkOnboardingRejected("ansible-runner is not installed in the management runtime")
    try:
        ServerCredentialCipher()
        credential_ref = plan["settings"]["credential_ref"]
        private_key_file = OperationService._credential_files().get(credential_ref)
        if not private_key_file:
            raise BulkOnboardingRejected(
                f"credential reference is not mapped to a private key: {credential_ref}"
            )
        public_key_from_private_file(private_key_file)
        database = database or Database()
        catalog = ManagedOperationCatalog(database=database)
        operation = catalog.get("server.bootstrap_ssh")
        catalog.validate_parameters("server.bootstrap_ssh", {})
    except BulkOnboardingRejected:
        raise
    except Exception as exc:
        raise BulkOnboardingRejected(
            f"management bootstrap prerequisites are invalid: {redact_sensitive_text(exc)}"
        ) from exc
    if (
        operation.scope != "server"
        or operation.supports_batch
        or not operation.version_id
        or not operation.playbook.is_file()
        or not os.access(operation.playbook, os.R_OK)
        or any(not path.is_file() or not os.access(path, os.R_OK) for path in operation.source_paths)
    ):
        raise BulkOnboardingRejected("server.bootstrap_ssh published catalog contract is incompatible")
    context = PlatformPreflightContext(catalog=catalog, database=database)
    context.assert_no_active_runs(entries)
    return context


def verify_actionable_state(
    plan: Mapping[str, Any], current_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Reject stale actionable rows before prompting for or writing a password."""

    current_rows = [dict(row) for row in current_rows]
    current = {normalize_text(row.get("server_id")): row for row in current_rows}
    duplicate_internal_ips = find_duplicate_internal_ips(current_rows)
    settings = plan["settings"]
    actionable = []
    for entry in plan["servers"]:
        if entry["action"] != "onboard":
            continue
        row = current.get(entry["server_id"])
        if row is None:
            raise BulkOnboardingRejected(
                f"server is no longer an active onboarding target: {entry['server_id']}"
            )
        current_ip = parse_ip(row.get("internal_ip"))
        if current_ip is not None and str(current_ip) in duplicate_internal_ips:
            raise BulkOnboardingRejected(
                f"server state changed since planning: {entry['server_id']} "
                "(duplicate_internal_ip)"
            )
        classified = classify_server(row, settings)
        for name in (
            "server_row_version",
            "connection_row_version",
            "internal_ip",
            "classification",
        ):
            if classified.get(name) != entry.get(name):
                raise BulkOnboardingRejected(
                    f"server state changed since planning: {entry['server_id']} ({name})"
                )
        if classified["action"] != "onboard":
            raise BulkOnboardingRejected(
                f"server is no longer eligible for onboarding: {entry['server_id']}"
            )
        actionable.append(dict(entry))
    return actionable


def verify_fresh_scans(
    entries: Iterable[Mapping[str, Any]],
    *,
    port: int,
    max_workers: int = DEFAULT_WORKERS,
    scanner: Callable[[str, int], Mapping[str, Any]] = scan_ssh_host_keys,
) -> None:
    max_workers = validate_worker_count(max_workers)
    entries = list(entries)
    if not entries:
        return
    mismatches = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(entries))) as executor:
        futures = {
            executor.submit(scanner, entry["internal_ip"], port): entry
            for entry in entries
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                fresh = normalize_scan_result(entry["internal_ip"], port, future.result())
            except Exception:
                mismatches.append(entry["server_id"])
                continue
            if fresh != entry["scan"]:
                mismatches.append(entry["server_id"])
    if mismatches:
        raise BulkOnboardingRejected(
            "fresh SSH host key scan differs from the confirmed plan for: "
            + ", ".join(sorted(mismatches))
        )


def read_bootstrap_result(run_id: UUID, database: Database | None = None) -> dict[str, Any]:
    database = database or Database()
    row = database.fetch_one(
        """
        SELECT run.status::text AS run_status, host.status::text AS host_status,
               host.error_message
        FROM automation.operation_run run
        JOIN automation.operation_host_result host ON host.run_id = run.run_id
        WHERE run.run_id = %(run_id)s
        """,
        {"run_id": run_id},
    )
    if row is None:
        return {"status": "failed", "error": "bootstrap result was not persisted"}
    if row["run_status"] == "succeeded" and row["host_status"] == "succeeded":
        return {"status": "succeeded", "error": None}
    return {
        "status": "failed",
        "error": row["error_message"] or f"bootstrap ended with status {row['run_status']}",
    }


def _safe_error(value: Any, password: str) -> str:
    text = redact_sensitive_text(value) or "bulk onboarding failed"
    return text.replace(password, "[REDACTED]") if password else text


def onboard_one(
    entry: Mapping[str, Any],
    *,
    settings: Mapping[str, Any],
    password: str,
    actor_ref: str,
    server_service_factory: Callable[[], ServerManagementService],
    operation_service_factory: Callable[[], OperationService],
    result_reader: Callable[[UUID], Mapping[str, Any]],
) -> dict[str, Any]:
    server_id = UUID(entry["server_id"])
    run_id = None
    try:
        server_service_factory().save_connection(
            server_id,
            {
                "connection_type": "ssh",
                "host": entry["internal_ip"],
                "port": settings["port"],
                "username": settings["username"],
                "credential_ref": settings["credential_ref"],
                "ssh_password": password,
                "ssh_host_keys": list(entry["scan"]["host_keys"]),
                "use_become": settings["use_become"],
                "row_version": entry["connection_row_version"],
            },
            actor_ref,
        )
        operation_service = operation_service_factory()
        run_id = operation_service.create_run(
            "server.bootstrap_ssh",
            server_ids=[server_id],
            parameters={},
            requested_by=actor_ref,
        )
        operation_service.execute_run(run_id)
        outcome = result_reader(run_id)
        if outcome.get("status") == "succeeded":
            status, error = "succeeded", None
        else:
            status = "failed"
            error = _safe_error(outcome.get("error") or "bootstrap failed", password)
    except Exception as exc:
        status, error = "failed", _safe_error(exc, password)
    return {
        "server_id": entry["server_id"],
        "name": entry["name"],
        "management_host": entry["internal_ip"],
        "status": status,
        "run_id": str(run_id) if run_id is not None else None,
        "error": error,
    }


def apply_plan_document(
    plan: Mapping[str, Any],
    *,
    current_rows: Iterable[Mapping[str, Any]],
    accept_scanned_host_keys: bool,
    actor_ref: str,
    max_workers: int = DEFAULT_WORKERS,
    scanner: Callable[[str, int], Mapping[str, Any]] = scan_ssh_host_keys,
    preflight_checker: Callable[
        [Mapping[str, Any], Iterable[Mapping[str, Any]]],
        PlatformPreflightContext | None,
    ] = verify_platform_preconditions,
    state_reader: Callable[[], Iterable[Mapping[str, Any]]] = fetch_server_snapshot,
    password_reader: Callable[[str], str] = getpass.getpass,
    server_service_factory: Callable[[], ServerManagementService] = ServerManagementService,
    operation_service_factory: Callable[[], OperationService] = OperationService,
    result_reader: Callable[[UUID], Mapping[str, Any]] = read_bootstrap_result,
) -> dict[str, Any]:
    validate_plan_document(plan)
    max_workers = validate_worker_count(max_workers)
    actor_ref = normalize_text(actor_ref)
    if not actor_ref or len(actor_ref) > 200:
        raise BulkOnboardingRejected("actor_ref must be a non-empty value of at most 200 characters")
    if not accept_scanned_host_keys:
        raise BulkOnboardingRejected("--accept-scanned-host-keys is required")
    actionable = verify_actionable_state(plan, current_rows)
    preflight = preflight_checker(plan, actionable)
    if preflight is not None and operation_service_factory is OperationService:
        def operation_service_factory():
            return OperationService(catalog=preflight.catalog)
    verify_fresh_scans(
        actionable,
        port=plan["settings"]["port"],
        max_workers=max_workers,
        scanner=scanner,
    )
    if preflight is not None:
        actionable = verify_actionable_state(plan, state_reader())
        preflight.assert_no_active_runs(actionable)
    skipped = [
        {
            "server_id": entry["server_id"],
            "name": entry["name"],
            "management_host": entry.get("internal_ip"),
            "status": "skipped",
            "run_id": None,
            "error": None,
            "reason": entry["reason"],
        }
        for entry in plan["servers"]
        if entry["action"] == "skip"
    ]
    completed = []
    if actionable:
        password = password_reader("SSH password for all onboarding targets: ")
        if not isinstance(password, str) or not password:
            raise BulkOnboardingRejected("SSH password cannot be empty")
        confirmation = password_reader("Confirm SSH password: ")
        if not isinstance(confirmation, str) or not hmac.compare_digest(
            password, confirmation,
        ):
            raise BulkOnboardingRejected("SSH password confirmation does not match")
        confirmation = ""
        with ThreadPoolExecutor(max_workers=min(max_workers, len(actionable))) as executor:
            futures = [
                executor.submit(
                    onboard_one,
                    entry,
                    settings=plan["settings"],
                    password=password,
                    actor_ref=actor_ref,
                    server_service_factory=server_service_factory,
                    operation_service_factory=operation_service_factory,
                    result_reader=result_reader,
                )
                for entry in actionable
            ]
            completed = [future.result() for future in as_completed(futures)]
        password = ""  # Limit the lifetime of the only plaintext reference in this frame.
    results = sorted(skipped + completed, key=lambda item: item["server_id"])
    counts = Counter(item["status"] for item in results)
    return {
        "summary": {
            "total": len(results),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
        },
        "results": results,
    }


def plan_command(args: argparse.Namespace) -> dict[str, Any]:
    active_rows = fetch_server_snapshot()
    selected_rows = select_requested_servers(active_rows, getattr(args, "server_ids", None))
    plan = build_plan(
        selected_rows,
        username=args.username,
        port=args.port,
        credential_ref=args.credential_ref,
        use_become=args.use_become,
        scan_workers=args.scan_workers,
        duplicate_check_rows=active_rows,
    )
    digest = atomic_write_plan(args.output, plan)
    return {
        "ok": True,
        "plan_file": str(args.output),
        "plan_sha256": digest,
        **plan["summary"],
    }


def apply_command(args: argparse.Namespace) -> dict[str, Any]:
    if not args.accept_scanned_host_keys:
        raise BulkOnboardingRejected("--accept-scanned-host-keys is required")
    plan, digest = load_confirmed_plan(args.plan_file, args.plan_sha256)
    result = apply_plan_document(
        plan,
        current_rows=fetch_server_snapshot(),
        accept_scanned_host_keys=True,
        actor_ref=args.actor_ref,
        max_workers=args.workers,
    )
    return {"ok": result["summary"]["failed"] == 0, "plan_sha256": digest, **result}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="create a read-only, secret-free plan")
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--username", required=True)
    plan_parser.add_argument("--port", type=int, required=True)
    plan_parser.add_argument("--credential-ref", required=True)
    plan_parser.add_argument(
        "--server-id", dest="server_ids", action="append", type=UUID,
        help="active server UUID to include; repeat for a pilot subset",
    )
    plan_parser.add_argument("--use-become", action="store_true", default=False)
    plan_parser.add_argument("--scan-workers", type=int, default=DEFAULT_WORKERS)
    plan_parser.set_defaults(handler=plan_command)

    apply_parser = commands.add_parser("apply", help="apply an exactly confirmed plan")
    apply_parser.add_argument("--plan-file", type=Path, required=True)
    apply_parser.add_argument("--plan-sha256", required=True)
    apply_parser.add_argument(
        "--accept-scanned-host-keys", action="store_true", required=True,
    )
    apply_parser.add_argument("--actor-ref", required=True)
    apply_parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    apply_parser.set_defaults(handler=apply_command)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok", True) else 2
    except (BulkOnboardingRejected, OSError, ValueError, KeyError) as exc:
        print(f"Bulk server onboarding rejected: {redact_sensitive_text(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
