#!/usr/bin/env python3
"""Export the management database Gateway contract for the observability stack."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


DATABASE_ENV = "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL"
OTEL_ENDPOINT_ENV = "AGENTSWATCH_OTEL_ENDPOINT"


def build_inventory(rows, otel_endpoint: str, cluster: str | None = None) -> dict:
    endpoint = str(otel_endpoint or "").strip().rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("OTel endpoint must be an HTTP(S) URL")
    gateways = []
    gateway_ids = set()
    runtime_ids = set()
    for row in rows:
        if not row.get("monitoring_ready"):
            continue
        gateway_id = str(row["gateway_id"])
        runtime_id = str(row["runtime_id"])
        if gateway_id in gateway_ids:
            raise ValueError(f"duplicate gateway_id in integration view: {gateway_id}")
        if runtime_id in runtime_ids:
            raise ValueError(f"duplicate runtime_id in integration view: {runtime_id}")
        gateway_ids.add(gateway_id)
        runtime_ids.add(runtime_id)
        environment_code = str(row.get("environment_code") or "")
        gateways.append({
            "gateway_id": gateway_id,
            "runtime_id": runtime_id,
            "server_id": str(row["server_id"]),
            "server_display_id": row["server_display_id"],
            "host": row["monitor_host"],
            "port": row["monitor_port"],
            "enabled": bool(row["monitoring_enabled"]),
            "cluster": cluster or environment_code,
            "env": environment_code,
            "region": row.get("region") or "",
            "team": row.get("operations_team") or "",
            "deployment_mode": row["deployment_mode"],
            "lobster_name": row["lobster_name"],
            "department": row.get("department_name") or "",
            "display_name": row["display_name"],
            "native_scrape_expected": True,
        })
    gateways.sort(key=lambda item: (item["cluster"], item["gateway_id"]))
    return {"defaults": {"otel_endpoint": endpoint}, "gateways": gateways}


def write_inventory(path: Path, inventory: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export monitoring-ready Gateways from the management database read-only view.",
    )
    parser.add_argument("--output", default="cluster/lobsters.managed.json")
    parser.add_argument("--cluster", help="Override the cluster label; defaults to environment_code")
    parser.add_argument("--runtime-family", default="openclaw")
    parser.add_argument("--otel-endpoint", default=os.environ.get(OTEL_ENDPOINT_ENV, ""))
    args = parser.parse_args()
    database_url = os.environ.get(DATABASE_ENV, "").strip()
    if not database_url:
        print(f"{DATABASE_ENV} is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            if connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
                raise RuntimeError("observability integration database role must be read-only")
            rows = connection.execute(
                """
                SELECT *
                FROM integration.observability_gateway_v1
                WHERE runtime_family = %(runtime_family)s
                ORDER BY environment_code, gateway_id, runtime_id
                """,
                {"runtime_family": args.runtime_family},
            ).fetchall()
        inventory = build_inventory(rows, args.otel_endpoint, args.cluster)
        write_inventory(Path(args.output), inventory)
        excluded = len(rows) - len(inventory["gateways"])
        print(
            f"exported {len(inventory['gateways'])} monitoring-ready Gateways to {args.output}; "
            f"excluded {excluded} incomplete rows"
        )
        return 0
    except (OSError, psycopg.Error, RuntimeError, ValueError) as exc:
        print(f"Gateway inventory export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
