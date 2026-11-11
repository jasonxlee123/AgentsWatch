from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

import yaml

from scripts.generate_lobster_configs import (
    merged as merge_generator_item,
    render_otel_patch,
    render_scrape_configs,
)

from .config import ControlConfig
from .sources import LocalJsonSource, ManagementDatabaseSource, SnapshotInput


SCHEMA_VERSION = 1
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
SHARED_DIRECTORY_MODE = 0o2750
SHARED_FILE_MODE = 0o640
SAFE_OVERLAY_FIELDS = {
    "cluster",
    "env",
    "region",
    "team",
    "scheme",
    "metrics_path",
    "scrape_host",
    "scrape_port",
    "gateway_bind",
    "native_scrape_enabled",
    "sample_rate",
    "flush_interval_ms",
    "stuck_session_warn_ms",
    "stuck_session_abort_ms",
    "service_name",
    "plugins_allow",
}
FORBIDDEN_SECRET_NAMES = {
    "token",
    "password",
    "secret",
    "private_key",
    "credential",
    "credential_ref",
    "encrypted_credential",
}


class SnapshotConflict(RuntimeError):
    pass


class RevisionNotFound(RuntimeError):
    pass


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, UUID)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_json(payload: Any) -> str:
    return json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _port(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"port is outside 1..65535: {port}")
    return port


def _runtime_id_for_local(row: dict[str, Any]) -> tuple[str, bool]:
    runtime_id = _text(row.get("runtime_id"))
    if runtime_id:
        return runtime_id, False
    gateway_id = _text(row.get("gateway_id"))
    if not gateway_id:
        raise ValueError("standalone inventory row requires runtime_id or gateway_id")
    return f"standalone:{gateway_id}", True


def _blocked_reasons(row: dict[str, Any], ready_hint: bool | None = None) -> list[str]:
    reasons: list[str] = []
    if not row.get("gateway_id"):
        reasons.append("missing_gateway_id")
    if not row.get("primary_agent_id") or not row.get("lobster_name"):
        reasons.append("missing_primary_agent")
    if not row.get("monitor_host"):
        reasons.append("missing_monitor_host")
    if not row.get("monitor_port"):
        reasons.append("missing_monitor_port")
    if row.get("lifecycle_status") not in {None, "", "active"}:
        reasons.append("runtime_not_active")
    if row.get("monitoring_enabled") is False:
        reasons.append("monitoring_disabled")
    if ready_hint is False and not reasons:
        reasons.append("source_not_ready")
    return reasons


def _normalize_database_row(row: dict[str, Any]) -> dict[str, Any]:
    runtime_id = _text(row.get("runtime_id"))
    if not runtime_id:
        raise ValueError("management database snapshot contains a row without runtime_id")
    normalized = {
        "runtime_id": runtime_id,
        "runtime_id_source": "management-db",
        "gateway_id": _optional_text(row.get("gateway_id")),
        "gateway_id_source": _optional_text(row.get("gateway_id_source")),
        "gateway_id_type": _optional_text(row.get("gateway_id_type")),
        "server_id": _optional_text(row.get("server_id")),
        "server_display_id": _optional_text(row.get("server_display_id")),
        "agent_deployment_id": _optional_text(row.get("agent_deployment_id")),
        "primary_agent_id": _optional_text(row.get("primary_agent_id")),
        "lobster_name": _optional_text(row.get("lobster_name")),
        "department_code": _optional_text(row.get("department_code")),
        "department_name": _optional_text(row.get("department_name")),
        "runtime_name": _optional_text(row.get("runtime_name")),
        "runtime_family": _optional_text(row.get("runtime_family")) or "openclaw",
        "monitor_host": _optional_text(row.get("monitor_host")),
        "monitor_port": _port(row.get("monitor_port")),
        "deployment_mode": _optional_text(row.get("deployment_mode")) or "baremetal",
        "bind_address": _optional_text(row.get("bind_address")),
        "runtime_account": _optional_text(row.get("runtime_account")),
        "config_path": _optional_text(row.get("config_path")),
        "container_name": _optional_text(row.get("container_name")),
        "lifecycle_status": _optional_text(row.get("lifecycle_status")),
        "environment_lifecycle_status": _optional_text(
            row.get("environment_lifecycle_status")
        ),
        "server_lifecycle_status": _optional_text(row.get("server_lifecycle_status")),
        "runtime_lifecycle_status": _optional_text(
            row.get("runtime_lifecycle_status")
        ),
        "runtime_observed_status": _optional_text(row.get("runtime_observed_status")),
        "agent_deployment_lifecycle_status": _optional_text(
            row.get("agent_deployment_lifecycle_status")
        ),
        "primary_agent_lifecycle_status": _optional_text(
            row.get("primary_agent_lifecycle_status")
        ),
        "environment_code": _optional_text(row.get("environment_code")),
        "environment_stage": _optional_text(row.get("environment_stage")),
        "region": _optional_text(row.get("region")),
        "operations_team": _optional_text(row.get("operations_team")),
        "desired_version": _optional_text(row.get("desired_version")),
        "actual_version": _optional_text(row.get("actual_version")),
        "discovery_snapshot_id": _optional_text(row.get("discovery_snapshot_id")),
        "discovery_status": _optional_text(row.get("discovery_status")),
        "discovered_at": _json_value(row.get("discovered_at")),
        "discovery_content_hash": _optional_text(row.get("discovery_content_hash")),
        "display_name": _optional_text(row.get("display_name")),
        "monitoring_enabled": bool(row.get("monitoring_enabled")),
        "source_updated_at": _json_value(row.get("source_updated_at")),
        "source_revision": _json_value(row.get("source_revision")),
        "contract_content_hash": _optional_text(row.get("contract_content_hash")),
        "identity_ready": bool(row.get("identity_ready")),
        "lifecycle_ready": bool(row.get("lifecycle_ready")),
        "otel_configuration_ready": bool(row.get("otel_configuration_ready")),
        "prometheus_target_ready": bool(row.get("prometheus_target_ready")),
        "otel_ready": bool(row.get("otel_ready")),
        "prometheus_ready": bool(row.get("prometheus_ready")),
        "monitoring_eligible": bool(row.get("monitoring_eligible")),
        "source_present": True,
    }
    if not normalized["display_name"] and normalized["lobster_name"]:
        normalized["display_name"] = "-".join(
            value
            for value in (
                normalized["lobster_name"],
                normalized["server_display_id"],
            )
            if value
        )
    source_reasons = row.get("readiness_reasons") or []
    if not isinstance(source_reasons, (list, tuple)):
        raise ValueError(f"runtime {runtime_id} readiness_reasons must be an array")
    reasons = [str(reason) for reason in source_reasons]
    normalized["blocked_reasons"] = reasons
    normalized["state"] = (
        "active" if normalized["monitoring_eligible"] else "blocked"
    )
    normalized["deployable"] = normalized["monitoring_eligible"]
    normalized["native_scrape_enabled"] = False
    return normalized


def _normalize_local_row(
    row: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    item = {**defaults, **row}
    runtime_id, synthetic_runtime = _runtime_id_for_local(item)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    gateway_id = _optional_text(item.get("gateway_id"))
    lobster_name = _optional_text(item.get("lobster_name"))
    primary_agent_id = _optional_text(item.get("primary_agent_id"))
    if not primary_agent_id and lobster_name:
        primary_agent_id = f"standalone-agent:{gateway_id or runtime_id}"
    server_id = _optional_text(item.get("server_id") or metadata.get("server_id"))
    server_display_id = _optional_text(
        item.get("server_display_id") or metadata.get("server_id") or server_id
    )
    enabled = item.get("enabled", True) is not False
    normalized = {
        "runtime_id": runtime_id,
        "runtime_id_source": "synthetic" if synthetic_runtime else "local-json",
        "gateway_id": gateway_id,
        "gateway_id_source": "local-json",
        "gateway_id_type": "gateway_id",
        "server_id": server_id,
        "server_display_id": server_display_id,
        "agent_deployment_id": _optional_text(item.get("agent_deployment_id")),
        "primary_agent_id": primary_agent_id,
        "lobster_name": lobster_name,
        "department_code": _optional_text(item.get("department_code")),
        "department_name": _optional_text(item.get("department")),
        "runtime_name": _optional_text(item.get("runtime_name")) or gateway_id,
        "runtime_family": _optional_text(item.get("runtime_family")) or "openclaw",
        "monitor_host": _optional_text(item.get("host")),
        "monitor_port": _port(item.get("port"), 16402),
        "deployment_mode": _optional_text(item.get("deployment_mode")) or "baremetal",
        "lifecycle_status": "active" if enabled else "disabled",
        "environment_code": _optional_text(item.get("env")),
        "environment_stage": _optional_text(item.get("environment_stage")),
        "region": _optional_text(item.get("region")),
        "operations_team": _optional_text(item.get("team")),
        "desired_version": _optional_text(item.get("desired_version")),
        "actual_version": _optional_text(item.get("actual_version")),
        "display_name": _optional_text(item.get("display_name")),
        "monitoring_enabled": enabled,
        "source_updated_at": _json_value(item.get("source_updated_at")),
        "source_present": True,
        "native_scrape_enabled": bool(item.get("native_scrape_enabled", False)),
    }
    if not normalized["display_name"] and lobster_name:
        normalized["display_name"] = "-".join(
            value for value in (lobster_name, server_display_id) if value
        )
    reasons = _blocked_reasons(normalized)
    identity_ready = bool(gateway_id and primary_agent_id and lobster_name)
    lifecycle_ready = enabled
    otel_ready = identity_ready and lifecycle_ready
    prometheus_ready = identity_ready and lifecycle_ready and bool(
        normalized["monitor_host"] and normalized["monitor_port"]
    )
    normalized.update(
        {
            "identity_ready": identity_ready,
            "lifecycle_ready": lifecycle_ready,
            "otel_configuration_ready": otel_ready,
            "prometheus_target_ready": bool(
                normalized["monitor_host"] and normalized["monitor_port"]
            ),
            "otel_ready": otel_ready,
            "prometheus_ready": prometheus_ready,
            "monitoring_eligible": otel_ready or prometheus_ready,
            "contract_content_hash": None,
        }
    )
    normalized["blocked_reasons"] = reasons
    normalized["state"] = (
        "active" if normalized["monitoring_eligible"] else "blocked"
    )
    normalized["deployable"] = normalized["monitoring_eligible"]
    for key in SAFE_OVERLAY_FIELDS:
        if key in item:
            normalized[key] = _json_value(item[key])
    normalized["native_scrape_enabled"] = bool(
        normalized.get("native_scrape_enabled", False)
    )
    return normalized


def normalize_snapshot(source: SnapshotInput) -> list[dict[str, Any]]:
    if source.source == "management-db":
        rows = [_normalize_database_row(row) for row in source.rows]
    elif source.source == "local-json":
        rows = [_normalize_local_row(row, source.defaults) for row in source.rows]
    else:
        raise ValueError(f"unsupported inventory source: {source.source}")
    by_runtime: dict[str, dict[str, Any]] = {}
    gateway_ids: dict[str, str] = {}
    for row in rows:
        runtime_id = row["runtime_id"]
        if runtime_id in by_runtime:
            raise ValueError(f"duplicate runtime_id in full snapshot: {runtime_id}")
        by_runtime[runtime_id] = row
        gateway_id = row.get("gateway_id")
        if gateway_id:
            if gateway_id in gateway_ids:
                raise ValueError(
                    f"duplicate gateway_id in full snapshot: {gateway_id} "
                    f"({gateway_ids[gateway_id]}, {runtime_id})"
                )
            gateway_ids[gateway_id] = runtime_id
    return [by_runtime[key] for key in sorted(by_runtime)]


def _load_overlay(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    runtimes = payload.get("runtimes", payload) if isinstance(payload, dict) else None
    if not isinstance(runtimes, dict):
        raise ValueError("observability overlay must be an object keyed by runtime_id")
    result: dict[str, dict[str, Any]] = {}
    for runtime_id, values in runtimes.items():
        if not isinstance(values, dict):
            raise ValueError(f"overlay for runtime {runtime_id} must be an object")
        unknown = set(values) - SAFE_OVERLAY_FIELDS
        if unknown:
            raise ValueError(
                f"overlay for runtime {runtime_id} contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        normalized = _json_value(values)
        if "native_scrape_enabled" in normalized and not isinstance(
            normalized["native_scrape_enabled"], bool
        ):
            raise ValueError(
                f"overlay for runtime {runtime_id} native_scrape_enabled must be a boolean"
            )
        if "scrape_host" in normalized:
            scrape_host = normalized["scrape_host"]
            if not isinstance(scrape_host, str) or not scrape_host.strip():
                raise ValueError(
                    f"overlay for runtime {runtime_id} scrape_host must be a non-empty string"
                )
            normalized["scrape_host"] = scrape_host.strip()
        result[str(runtime_id)] = normalized
    return result


def apply_overlay(
    rows: list[dict[str, Any]], overlay: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        merged = dict(row)
        merged.update(overlay.get(row["runtime_id"], {}))
        if merged.get("scrape_port") not in (None, ""):
            merged["scrape_port"] = _port(merged["scrape_port"])
        merged["native_scrape_enabled"] = bool(
            merged.get("native_scrape_enabled", False)
        )
        if merged["native_scrape_enabled"] and not merged.get("prometheus_ready"):
            merged["blocked_reasons"] = list(
                dict.fromkeys(
                    [*merged.get("blocked_reasons", []), "native_scrape_not_ready"]
                )
            )
        result.append(merged)
    return result


def _comparison_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"state", "deployable", "blocked_reasons", "source_present"}
    }


def reconcile_snapshot(
    rows: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_by_id = {row["runtime_id"]: dict(row) for row in rows}
    previous_by_id = {row["runtime_id"]: dict(row) for row in previous}
    effective: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for runtime_id in sorted(current_by_id):
        row = current_by_id[runtime_id]
        old = previous_by_id.get(runtime_id)
        if old is None:
            action = "add" if row["deployable"] else "retain_blocked"
        elif not row["deployable"]:
            action = "disable" if old.get("deployable") else "retain_blocked"
        elif not old.get("deployable"):
            action = "update"
        elif _comparison_row(old) != _comparison_row(row):
            action = "update"
        else:
            action = "noop"
        effective.append(row)
        reasons = row.get("blocked_reasons", [])
        actions.append(
            {
                "runtime_id": runtime_id,
                "gateway_id": row.get("gateway_id"),
                "primary_agent_id": row.get("primary_agent_id"),
                "lobster_name": row.get("lobster_name"),
                "server_display_id": row.get("server_display_id"),
                "display_name": row.get("display_name"),
                "action": action,
                "state": row["state"],
                "blocked_reasons": reasons,
                "reason": reasons[0] if reasons else None,
            }
        )
    for runtime_id in sorted(set(previous_by_id) - set(current_by_id)):
        orphan = dict(previous_by_id[runtime_id])
        orphan.update(
            {
                "source_present": False,
                "monitoring_enabled": False,
                "deployable": False,
                "state": "orphan",
                "blocked_reasons": ["missing_from_source"],
                "native_scrape_enabled": False,
            }
        )
        effective.append(orphan)
        actions.append(
            {
                "runtime_id": runtime_id,
                "gateway_id": orphan.get("gateway_id"),
                "primary_agent_id": orphan.get("primary_agent_id"),
                "lobster_name": orphan.get("lobster_name"),
                "server_display_id": orphan.get("server_display_id"),
                "display_name": orphan.get("display_name"),
                "action": "orphan",
                "state": "orphan",
                "blocked_reasons": ["missing_from_source"],
                "reason": "missing_from_source",
            }
        )
    effective.sort(key=lambda item: item["runtime_id"])
    actions.sort(key=lambda item: item["runtime_id"])
    return effective, actions


def _summary(rows: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = {
        name: sum(1 for item in actions if item["action"] == name)
        for name in (
            "add",
            "update",
            "disable",
            "retain_blocked",
            "orphan",
            "noop",
        )
    }
    return {
        "total": len(rows),
        "deployable": sum(1 for row in rows if row.get("deployable")),
        "blocked": sum(1 for row in rows if row.get("state") == "blocked"),
        "orphan": sum(1 for row in rows if row.get("state") == "orphan"),
        "native_scrape_enabled": sum(
            1
            for row in rows
            if row.get("prometheus_ready") and row.get("native_scrape_enabled")
        ),
        **action_counts,
        "actions": action_counts,
    }


def _yaml_rule_artifact(rows: list[dict[str, Any]]) -> str:
    rules = []
    for row in rows:
        labels = {
            "runtime_id": _text(row.get("runtime_id")),
            "gateway_id": _text(row.get("gateway_id")),
            "server_id": _text(row.get("server_id")),
            "primary_agent_id": _text(row.get("primary_agent_id")),
            "agent_deployment_id": _text(row.get("agent_deployment_id")),
            "lobster_name": _text(row.get("lobster_name")),
            "server_display_id": _text(row.get("server_display_id")),
            "display_name": _text(row.get("display_name")),
            "cluster": _text(
                row.get("cluster") or row.get("environment_code") or "default"
            ),
            "env": _text(row.get("env") or row.get("environment_code")),
            "department": _text(row.get("department_name")),
            "region": _text(row.get("region")),
            "team": _text(row.get("team") or row.get("operations_team")),
            "deployment_mode": _text(row.get("deployment_mode")),
            "inventory_state": _text(row.get("state")),
        }
        rules.append(
            {
                "record": "agentswatch_gateway_info",
                "expr": "vector(1)",
                "labels": labels,
            }
        )
    payload = {
        "groups": [
            {
                "name": "agentswatch-gateway-inventory",
                "interval": "30s",
                "rules": rules,
            }
        ]
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _generator_item(row: dict[str, Any], otel_endpoint: str) -> dict[str, Any]:
    item = {
        "gateway_id": row["gateway_id"],
        "runtime_id": row["runtime_id"],
        "server_id": row.get("server_id") or "",
        "primary_agent_id": row.get("primary_agent_id") or "",
        "agent_deployment_id": row.get("agent_deployment_id") or "",
        "server_display_id": row.get("server_display_id") or "",
        "host": row.get("scrape_host") or row.get("monitor_host") or "127.0.0.1",
        "port": row.get("scrape_port") or row.get("monitor_port") or 16402,
        "enabled": True,
        "cluster": row.get("cluster") or row.get("environment_code") or "default",
        "env": row.get("env") or row.get("environment_code") or "",
        "region": row.get("region") or "",
        "team": row.get("team") or row.get("operations_team") or "",
        "deployment_mode": row.get("deployment_mode") or "baremetal",
        "lobster_name": row.get("lobster_name") or "",
        "department": row.get("department_name") or "",
        "display_name": row.get("display_name") or "",
        "otel_endpoint": row.get("otel_endpoint") or otel_endpoint,
        "native_scrape_expected": bool(row.get("native_scrape_enabled", False)),
    }
    for key in SAFE_OVERLAY_FIELDS - {"native_scrape_enabled", "env", "region", "team", "cluster"}:
        if key in row:
            item[key] = row[key]
    merged = merge_generator_item({}, item)
    if not row.get("scrape_host") and not row.get("monitor_host"):
        merged["host"] = None
    if not row.get("scrape_port") and not row.get("monitor_port"):
        merged["port"] = None
    return merged


def _prometheus_escape(value: Any) -> str:
    return (
        _text(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def gateway_info_exposition(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# HELP agentswatch_gateway_info Current observability inventory identity projection.",
        "# TYPE agentswatch_gateway_info gauge",
    ]
    for row in sorted(rows, key=lambda item: item["runtime_id"]):
        labels = {
            "runtime_id": row.get("runtime_id"),
            "gateway_id": row.get("gateway_id"),
            "server_id": row.get("server_id"),
            "primary_agent_id": row.get("primary_agent_id"),
            "agent_deployment_id": row.get("agent_deployment_id"),
            "lobster_name": row.get("lobster_name"),
            "server_display_id": row.get("server_display_id"),
            "display_name": row.get("display_name"),
            "cluster": row.get("cluster")
            or row.get("environment_code")
            or "default",
            "env": row.get("env") or row.get("environment_code"),
            "department": row.get("department_name"),
            "region": row.get("region"),
            "team": row.get("team") or row.get("operations_team"),
            "deployment_mode": row.get("deployment_mode"),
            "inventory_state": row.get("state"),
        }
        rendered = ",".join(
            f'{key}="{_prometheus_escape(value)}"' for key, value in labels.items()
        )
        lines.append(f"agentswatch_gateway_info{{{rendered}}} 1")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, SHARED_FILE_MODE)


def _write_shared_text(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")
    os.chmod(path, SHARED_FILE_MODE)


def _chmod_shared_directory(path: Path) -> None:
    try:
        os.chmod(path, SHARED_DIRECTORY_MODE)
    except PermissionError:
        if os.uname().sysname != "Darwin":
            raise
        # Darwin does not consistently allow a non-root process to set setgid
        # on bind-mounted directories. The writer's primary group is retained.
        os.chmod(path, 0o750)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparedSnapshot:
    source: str
    source_rows: list[dict[str, Any]]
    effective_rows: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    snapshot_hash: str
    canonical_hash: str
    summary: dict[str, Any]
    otel_endpoint: str


class InventoryController:
    def __init__(self, config: ControlConfig | None = None):
        self.config = config or ControlConfig.from_env()
        self.root = self.config.state_root
        self.revisions = self.root / "revisions"
        self.staging = self.root / "staging"
        self.current_link = self.root / "current"
        self.last_good_link = self.root / "last-known-good"
        self.lock_path = self.root / ".inventory.lock"
        self.status_path = self.root / "status.json"

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        if self.revisions.is_symlink() or self.staging.is_symlink():
            raise ValueError("observability state layout contains a symlinked work directory")
        self.revisions.mkdir(mode=0o750, exist_ok=True)
        self.staging.mkdir(mode=0o750, exist_ok=True)
        _chmod_shared_directory(self.root)
        _chmod_shared_directory(self.revisions)
        _chmod_shared_directory(self.staging)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.ensure_layout()
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _source(self, source: str) -> SnapshotInput:
        if source == "management-db":
            return ManagementDatabaseSource(
                self.config.database_url, self.config.runtime_family
            ).load()
        if source == "local-json":
            return LocalJsonSource(self.config.local_inventory_path).load()
        raise ValueError("source must be management-db or local-json")

    def _resolve_link(self, link: Path) -> Path | None:
        if not link.is_symlink() or self.revisions.is_symlink():
            return None
        raw_target = Path(os.readlink(link))
        if raw_target.is_absolute():
            return None
        try:
            target = (link.parent / raw_target).resolve(strict=True)
            revisions = self.revisions.resolve(strict=True)
            relative = target.relative_to(revisions)
        except (FileNotFoundError, RuntimeError, ValueError):
            return None
        if (
            len(relative.parts) != 1
            or not REVISION_PATTERN.fullmatch(relative.name)
            or not target.is_dir()
        ):
            return None
        return target

    def _active_revision_path(self) -> Path | None:
        return self._resolve_link(self.current_link)

    def _active_manifest(self) -> dict[str, Any] | None:
        path = self._active_revision_path()
        if path is None or not (path / "manifest.json").is_file():
            return None
        return json.loads((path / "manifest.json").read_text(encoding="utf-8"))

    def _active_snapshot(self) -> list[dict[str, Any]]:
        path = self._active_revision_path()
        if path is None or not (path / "snapshot.json").is_file():
            return []
        payload = json.loads((path / "snapshot.json").read_text(encoding="utf-8"))
        return payload.get("rows", [])

    def _prepare(self, source: str) -> PreparedSnapshot:
        source_input = self._source(source)
        source_rows = normalize_snapshot(source_input)
        overlay = _load_overlay(self.config.overlay_path)
        source_rows = apply_overlay(source_rows, overlay)
        effective, actions = reconcile_snapshot(source_rows, self._active_snapshot())
        endpoint = self.config.otel_endpoint
        if not endpoint and source_input.source == "local-json":
            endpoint = _text(source_input.defaults.get("otel_endpoint"))
        if not endpoint:
            raise ValueError("AGENTSWATCH_OTEL_ENDPOINT is required")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("AGENTSWATCH_OTEL_ENDPOINT must be an HTTP(S) URL")
        if source == "management-db":
            for row in source_rows:
                content_hash = row.get("contract_content_hash")
                if not content_hash or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
                    raise ValueError(
                        f"runtime {row['runtime_id']} has an invalid contract_content_hash"
                    )
            snapshot_rows = [
                {
                    "runtime_id": row["runtime_id"],
                    "contract_content_hash": row["contract_content_hash"],
                }
                for row in source_rows
            ]
        else:
            snapshot_rows = source_rows
        snapshot_payload = {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "rows": snapshot_rows,
        }
        semantic_rows = [
            {
                key: value
                for key, value in row.items()
                if key not in {"source_updated_at", "source_revision", "discovered_at"}
            }
            for row in effective
        ]
        canonical_payload = {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "otel_endpoint": endpoint.rstrip("/"),
            "rows": semantic_rows,
        }
        return PreparedSnapshot(
            source=source,
            source_rows=source_rows,
            effective_rows=effective,
            actions=actions,
            snapshot_hash=canonical_hash(snapshot_payload),
            canonical_hash=canonical_hash(canonical_payload),
            summary=_summary(effective, actions),
            otel_endpoint=endpoint.rstrip("/"),
        )

    @staticmethod
    def _check_expected(prepared: PreparedSnapshot, expected: str | None) -> None:
        if expected and expected != prepared.snapshot_hash:
            raise SnapshotConflict(
                "source snapshot changed after preview: "
                f"expected {expected}, got {prepared.snapshot_hash}"
            )

    @staticmethod
    def _activation_blockers(
        prepared: PreparedSnapshot,
        active_rows: list[dict[str, Any]],
    ) -> tuple[list[str], list[str]]:
        source_blockers = []
        if prepared.summary["total"] == 0:
            source_blockers.append("source snapshot is empty")
        if prepared.summary["deployable"] == 0:
            source_blockers.append("source snapshot has no deployable runtimes")

        active_deployable = {
            row["runtime_id"] for row in active_rows if row.get("deployable")
        }
        prepared_deployable = {
            row["runtime_id"]
            for row in prepared.effective_rows
            if row.get("deployable")
        }
        removed = sorted(active_deployable - prepared_deployable)
        destructive_blockers = []
        if removed:
            destructive_blockers.append(
                f"source snapshot would remove {len(removed)} previously deployable runtimes"
            )
        return source_blockers, destructive_blockers

    def _activation_allowed(
        self,
        source_blockers: list[str],
        destructive_blockers: list[str],
    ) -> bool:
        return (
            not source_blockers or self.config.allow_empty_inventory
        ) and (
            not destructive_blockers or self.config.allow_destructive_inventory
        )

    def preview(
        self, source: str = "management-db", expected_snapshot_hash: str | None = None
    ) -> dict[str, Any]:
        prepared = self._prepare(source)
        self._check_expected(prepared, expected_snapshot_hash)
        active = self._active_manifest()
        source_blockers, destructive_blockers = self._activation_blockers(
            prepared, self._active_snapshot()
        )
        activation_blockers = [*source_blockers, *destructive_blockers]
        return {
            "source": prepared.source,
            "snapshot_hash": prepared.snapshot_hash,
            "canonical_hash": prepared.canonical_hash,
            "active_revision_id": active.get("revision_id") if active else None,
            "changed": not active
            or active.get("canonical_hash") != prepared.canonical_hash,
            "summary": prepared.summary,
            "activation_allowed": self._activation_allowed(
                source_blockers, destructive_blockers
            ),
            "activation_blockers": activation_blockers,
            "changes": prepared.actions,
            "actions": prepared.actions,
        }

    def _build_revision(self, prepared: PreparedSnapshot, directory: Path) -> dict[str, Any]:
        eligible = [row for row in prepared.effective_rows if row.get("deployable")]
        generator_items = [
            _generator_item(row, prepared.otel_endpoint) for row in eligible
        ]
        native_items = [
            item
            for item, row in zip(generator_items, eligible)
            if row.get("native_scrape_enabled") and row.get("prometheus_ready")
        ]
        directory.mkdir(parents=True, mode=0o750)
        _chmod_shared_directory(directory)
        patches_dir = directory / "openclaw-monitoring-patches"
        patches_dir.mkdir(mode=0o750)
        _chmod_shared_directory(patches_dir)
        _write_json(
            directory / "snapshot.json",
            {
                "schema_version": SCHEMA_VERSION,
                "source": prepared.source,
                "snapshot_hash": prepared.snapshot_hash,
                "rows": prepared.effective_rows,
            },
        )
        _write_json(
            directory / "inventory.json",
            {
                "defaults": {"otel_endpoint": prepared.otel_endpoint},
                "gateways": [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "native_scrape_expected"
                    }
                    | {
                        "native_scrape_enabled": bool(
                            row.get("native_scrape_enabled")
                        ),
                        "native_scrape_expected": bool(
                            row.get("native_scrape_enabled")
                            and row.get("prometheus_ready")
                        ),
                        "otel_ready": bool(row.get("otel_ready")),
                        "prometheus_ready": bool(row.get("prometheus_ready")),
                    }
                    for item, row in zip(generator_items, eligible)
                ],
            },
        )
        _write_shared_text(
            directory / "openclaw-gateways.yml",
            render_scrape_configs(native_items),
        )
        _write_shared_text(
            directory / "agentswatch-gateway-info.rules.yml",
            _yaml_rule_artifact(prepared.effective_rows),
        )
        for item, row in zip(generator_items, eligible):
            if not row.get("otel_ready"):
                continue
            _write_shared_text(
                patches_dir / f"{item['gateway_id']}.json",
                render_otel_patch(item),
            )
        artifact_paths = [
            directory / "snapshot.json",
            directory / "inventory.json",
            directory / "openclaw-gateways.yml",
            directory / "agentswatch-gateway-info.rules.yml",
            *sorted(patches_dir.glob("*.json")),
        ]
        return {
            str(path.relative_to(directory)): _file_hash(path)
            for path in artifact_paths
        }

    @staticmethod
    def _validate_revision(directory: Path, expected_rows: int) -> None:
        snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        inventory = json.loads((directory / "inventory.json").read_text(encoding="utf-8"))
        scrape = yaml.safe_load(
            (directory / "openclaw-gateways.yml").read_text(encoding="utf-8")
        )
        info_rules = yaml.safe_load(
            (directory / "agentswatch-gateway-info.rules.yml").read_text(
                encoding="utf-8"
            )
        )
        if len(snapshot.get("rows", [])) != expected_rows:
            raise ValueError("staged full snapshot row count changed during validation")
        if not isinstance(inventory.get("gateways"), list):
            raise ValueError("staged deployable inventory is invalid")
        if not isinstance(scrape, dict) or not isinstance(scrape.get("scrape_configs"), list):
            raise ValueError("staged Prometheus scrape configuration is invalid")
        groups = info_rules.get("groups") if isinstance(info_rules, dict) else None
        if not isinstance(groups, list) or len(groups) != 1:
            raise ValueError("staged gateway info recording rules are invalid")
        rules = groups[0].get("rules") or []
        if len(rules) != expected_rows:
            raise ValueError("gateway info rules do not cover the full snapshot")
        for row in snapshot["rows"]:
            if FORBIDDEN_SECRET_NAMES.intersection(row):
                raise ValueError("staged snapshot contains a forbidden secret field")
        for patch in (directory / "openclaw-monitoring-patches").glob("*.json"):
            payload = json.loads(patch.read_text(encoding="utf-8"))
            capture = payload["diagnostics"]["otel"]["captureContent"]
            if capture.get("enabled") is not False:
                raise ValueError(f"content capture must remain disabled: {patch.name}")

    def _atomic_link(self, link: Path, revision_path: Path) -> None:
        relative_target = os.path.relpath(revision_path, link.parent)
        temporary = link.parent / f".{link.name}.{os.getpid()}.tmp"
        temporary.unlink(missing_ok=True)
        os.symlink(relative_target, temporary)
        os.replace(temporary, link)

    def _write_status(self, payload: dict[str, Any]) -> None:
        self.ensure_layout()
        fd, temporary_name = tempfile.mkstemp(prefix=".status.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_json_value(payload), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.status_path)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    def _reload_prometheus(self) -> None:
        if not self.config.prometheus_reload_url:
            return
        request = Request(self.config.prometheus_reload_url, method="POST")
        with urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(
                    f"Prometheus reload returned HTTP {response.status}"
                )

    def sync(
        self, source: str = "management-db", expected_snapshot_hash: str | None = None
    ) -> dict[str, Any]:
        with self.lock():
            prepared = self._prepare(source)
            self._check_expected(prepared, expected_snapshot_hash)
            source_blockers, destructive_blockers = self._activation_blockers(
                prepared, self._active_snapshot()
            )
            activation_blockers = [*source_blockers, *destructive_blockers]
            if not self._activation_allowed(source_blockers, destructive_blockers):
                override_names = []
                if source_blockers and not self.config.allow_empty_inventory:
                    override_names.append(
                        "AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY"
                    )
                if (
                    destructive_blockers
                    and not self.config.allow_destructive_inventory
                ):
                    override_names.append(
                        "AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY"
                    )
                raise ValueError(
                    "refusing to activate an unsafe observability snapshot: "
                    + "; ".join(activation_blockers)
                    + ". Preview and repair the management inventory; required emergency "
                    "override(s) are disabled by default: "
                    + ", ".join(override_names)
                )
            active = self._active_manifest()
            if active and active.get("canonical_hash") == prepared.canonical_hash:
                return {
                    "changed": False,
                    "revision_id": active["revision_id"],
                    "snapshot_hash": prepared.snapshot_hash,
                    "canonical_hash": prepared.canonical_hash,
                    "summary": prepared.summary,
                    "actions": prepared.actions,
                    "changes": prepared.actions,
                }
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            revision_id = f"{timestamp}-{prepared.canonical_hash[:12]}"
            stage = self.staging / f".{revision_id}.{os.getpid()}"
            revision_path = self.revisions / revision_id
            if stage.exists():
                shutil.rmtree(stage)
            try:
                artifacts = self._build_revision(prepared, stage)
                self._validate_revision(stage, len(prepared.effective_rows))
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "revision_id": revision_id,
                    "source": prepared.source,
                    "snapshot_hash": prepared.snapshot_hash,
                    "canonical_hash": prepared.canonical_hash,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "summary": prepared.summary,
                    "artifacts": artifacts,
                }
                _write_json(stage / "manifest.json", manifest)
                os.replace(stage, revision_path)
            except Exception:
                if stage.exists():
                    shutil.rmtree(stage)
                raise
            previous_path = self._active_revision_path()
            if previous_path is not None:
                self._atomic_link(self.last_good_link, previous_path)
            self._atomic_link(self.current_link, revision_path)
            try:
                self._reload_prometheus()
            except Exception:
                if previous_path is not None:
                    self._atomic_link(self.current_link, previous_path)
                else:
                    self.current_link.unlink(missing_ok=True)
                raise
            if previous_path is None:
                self._atomic_link(self.last_good_link, revision_path)
            self._write_status(
                {
                    "active_revision_id": revision_id,
                    "last_known_good_revision_id": (
                        previous_path.name if previous_path else revision_id
                    ),
                    "source": prepared.source,
                    "snapshot_hash": prepared.snapshot_hash,
                    "canonical_hash": prepared.canonical_hash,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                    "summary": prepared.summary,
                }
            )
            return {
                "changed": True,
                "revision_id": revision_id,
                "snapshot_hash": prepared.snapshot_hash,
                "canonical_hash": prepared.canonical_hash,
                "summary": prepared.summary,
                "actions": prepared.actions,
                "changes": prepared.actions,
            }

    def metrics(self) -> str:
        return gateway_info_exposition(self._active_snapshot())

    def status(self) -> dict[str, Any]:
        layout_error = None
        try:
            self.ensure_layout()
        except ValueError as exc:
            layout_error = str(exc)
        if self.status_path.is_file():
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        else:
            payload = {
                "active_revision_id": None,
                "last_known_good_revision_id": None,
                "source": None,
                "snapshot_hash": None,
                "canonical_hash": None,
                "last_sync_at": None,
                "summary": {
                    "total": 0,
                    "deployable": 0,
                    "blocked": 0,
                    "orphan": 0,
                    "native_scrape_enabled": 0,
                },
            }
        payload.update(
            {
                "state_root": str(self.root),
                "current_path": str(self.current_link),
                "healthy": not layout_error and bool(self._active_revision_path()),
            }
        )
        if layout_error:
            payload["state_error"] = layout_error
        return payload

    def rollback(self, revision_id: str) -> dict[str, Any]:
        if not REVISION_PATTERN.fullmatch(revision_id):
            raise RevisionNotFound("invalid revision_id")
        with self.lock():
            target = self.revisions / revision_id
            if not target.is_dir() or not (target / "manifest.json").is_file():
                raise RevisionNotFound(f"revision not found: {revision_id}")
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self._validate_revision(target, manifest["summary"]["total"])
            previous = self._active_revision_path()
            if previous is not None and previous != target:
                self._atomic_link(self.last_good_link, previous)
            self._atomic_link(self.current_link, target)
            try:
                self._reload_prometheus()
            except Exception:
                if previous is not None:
                    self._atomic_link(self.current_link, previous)
                raise
            self._write_status(
                {
                    "active_revision_id": revision_id,
                    "last_known_good_revision_id": (
                        previous.name if previous and previous != target else None
                    ),
                    "source": manifest["source"],
                    "snapshot_hash": manifest["snapshot_hash"],
                    "canonical_hash": manifest["canonical_hash"],
                    "last_sync_at": manifest["created_at"],
                    "last_rollback_at": datetime.now(timezone.utc).isoformat(),
                    "summary": manifest["summary"],
                }
            )
            return {
                "changed": previous != target,
                "revision_id": revision_id,
                "rolled_back_from": previous.name if previous else None,
                "snapshot_hash": manifest["snapshot_hash"],
                "canonical_hash": manifest["canonical_hash"],
                "summary": manifest["summary"],
            }
