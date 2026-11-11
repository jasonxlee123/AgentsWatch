from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class SnapshotInput:
    source: str
    defaults: dict[str, Any]
    rows: list[dict[str, Any]]


DATABASE_COLUMNS = (
    "runtime_id",
    "gateway_id",
    "gateway_id_source",
    "gateway_id_type",
    "server_id",
    "server_display_id",
    "agent_deployment_id",
    "primary_agent_id",
    "lobster_name",
    "department_code",
    "department_name",
    "runtime_name",
    "runtime_family",
    "monitor_host",
    "monitor_port",
    "deployment_mode",
    "bind_address",
    "runtime_account",
    "config_path",
    "container_name",
    "lifecycle_status",
    "environment_lifecycle_status",
    "server_lifecycle_status",
    "runtime_lifecycle_status",
    "runtime_observed_status",
    "agent_deployment_lifecycle_status",
    "primary_agent_lifecycle_status",
    "environment_code",
    "environment_stage",
    "region",
    "operations_team",
    "desired_version",
    "actual_version",
    "discovery_snapshot_id",
    "discovery_status",
    "discovered_at",
    "discovery_content_hash",
    "display_name",
    "monitoring_enabled",
    "monitoring_ready",
    "identity_ready",
    "lifecycle_ready",
    "otel_configuration_ready",
    "prometheus_target_ready",
    "otel_ready",
    "prometheus_ready",
    "monitoring_eligible",
    "readiness_reasons",
    "source_updated_at",
    "source_revision",
    "contract_content_hash",
)


class ManagementDatabaseSource:
    def __init__(self, database_url: str, runtime_family: str = "openclaw"):
        self.database_url = database_url.strip()
        self.runtime_family = runtime_family

    def load(self) -> SnapshotInput:
        if not self.database_url:
            raise ValueError(
                "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL is required"
            )
        columns = ", ".join(DATABASE_COLUMNS)
        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=10,
            application_name="agentswatch-observability-control",
        ) as connection:
            read_only = connection.execute("SHOW transaction_read_only").fetchone()
            if not read_only or read_only["transaction_read_only"] != "on":
                raise RuntimeError(
                    "observability integration database role must be read-only"
                )
            rows = connection.execute(
                f"""
                SELECT {columns}
                FROM integration.observability_gateway_v2
                WHERE runtime_family = %(runtime_family)s
                ORDER BY runtime_id
                """,
                {"runtime_family": self.runtime_family},
            ).fetchall()
        return SnapshotInput(
            source="management-db",
            defaults={},
            rows=[dict(row) for row in rows],
        )


class LocalJsonSource:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> SnapshotInput:
        if not self.path.is_file():
            raise FileNotFoundError(f"local inventory not found: {self.path}")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("local inventory root must be an object")
        defaults = payload.get("defaults") or {}
        rows = payload.get("gateways") or []
        if not isinstance(defaults, dict) or not isinstance(rows, list):
            raise ValueError("local inventory defaults must be an object and gateways an array")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("every local inventory gateway must be an object")
        return SnapshotInput(
            source="local-json",
            defaults=dict(defaults),
            rows=[dict(row) for row in rows],
        )
