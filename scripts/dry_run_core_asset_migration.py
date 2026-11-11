#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import sys
import uuid
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "cluster" / "lobsters.json"
MIGRATION_NAMESPACE = uuid.UUID("7d65b0fa-9e5a-4f08-9505-a57d53cc8f53")
PLACEHOLDER_VALUES = {"定向用户", "待定", "未知", "unknown", "n/a", "-"}


def stable_id(object_type, source_key):
    return str(uuid.uuid5(MIGRATION_NAMESPACE, f"{object_type}:{source_key}"))


def canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def duplicate_values(values):
    counts = Counter(value for value in values if value not in (None, ""))
    return {value: count for value, count in counts.items() if count > 1}


def add_issue(issues, severity, code, row_number, field, message):
    issues.append(
        {
            "severity": severity,
            "code": code,
            "row_number": row_number,
            "field": field,
            "message": message,
        }
    )


def validate_source_identity(gateways, issues):
    fields = {
        "gateway_id": [item.get("gateway_id") for item in gateways],
        "host": [item.get("host") for item in gateways],
        "provider_instance_id": [item.get("metadata", {}).get("server_id") for item in gateways],
    }
    for field, values in fields.items():
        for index, value in enumerate(values, start=1):
            if value in (None, ""):
                add_issue(issues, "error", "SOURCE_IDENTITY_REQUIRED", index, field, "identity value is required")
        for value, count in duplicate_values(values).items():
            add_issue(
                issues,
                "error",
                "SOURCE_IDENTITY_DUPLICATE",
                None,
                field,
                f"{value!r} occurs {count} times",
            )


def build_migration_plan(source):
    gateways = source.get("gateways", [])
    defaults = source.get("defaults", {})
    issues = []
    validate_source_identity(gateways, issues)

    object_ids = {name: [] for name in ("server", "runtime", "agent", "deployment", "member", "snapshot")}
    raw_hashes = []
    network_requirement_count = 0
    external_identifier_count = 0
    unresolved_main_keys = 0
    unresolved_service_archetypes = 0
    unresolved_department_codes = 0
    unresolved_admin_references = 0

    for row_number, gateway in enumerate(gateways, start=1):
        metadata = gateway.get("metadata") or {}
        gateway_id = gateway.get("gateway_id")
        server_key = metadata.get("server_id")
        raw_hashes.append(canonical_hash(gateway))

        if gateway_id and server_key:
            object_ids["server"].append(stable_id("server", server_key))
            object_ids["runtime"].append(stable_id("runtime", gateway_id))
            object_ids["agent"].append(stable_id("agent", gateway_id))
            object_ids["deployment"].append(stable_id("deployment", gateway_id))
            object_ids["member"].append(stable_id("deployment-member", gateway_id))
            object_ids["snapshot"].append(stable_id("runtime-snapshot", f"{gateway_id}:{raw_hashes[-1]}"))
            external_identifier_count += 2

        try:
            ipaddress.ip_address(gateway.get("host"))
        except ValueError:
            add_issue(issues, "error", "SOURCE_IP_INVALID", row_number, "host", "host must be an IP address")

        if gateway.get("deployment_mode") != "baremetal":
            add_issue(
                issues,
                "warning",
                "SOURCE_DEPLOYMENT_MODE_REVIEW",
                row_number,
                "deployment_mode",
                "legacy adapter is currently baselined for baremetal",
            )

        unresolved_service_archetypes += 1
        add_issue(
            issues,
            "warning",
            "SERVICE_ARCHETYPE_REQUIRED",
            row_number,
            "service_archetype",
            "legacy service_mode does not determine personal/department/business",
        )

        unresolved_department_codes += 1
        add_issue(
            issues,
            "warning",
            "DEPARTMENT_CODE_REQUIRED",
            row_number,
            "department",
            "department name must be mapped to a stable department code",
        )

        for field in ("business_owner", "it_owner"):
            unresolved_admin_references += 1
            add_issue(
                issues,
                "warning",
                "EMPLOYEE_NUMBER_REQUIRED",
                row_number,
                f"metadata.{field}",
                "person name must be mapped to an employee number",
            )

        unresolved_main_keys += 1
        add_issue(
            issues,
            "warning",
            "RUNTIME_AGENT_KEY_UNRESOLVED",
            row_number,
            "runtime_agent_key",
            "main runtime agent key requires read-only discovery",
        )

        target_user = str(metadata.get("target_user") or "").strip()
        if target_user.lower() in PLACEHOLDER_VALUES:
            add_issue(
                issues,
                "warning",
                "PLACEHOLDER_VALUE",
                row_number,
                "metadata.target_user",
                "placeholder cannot be migrated as a person identifier",
            )

        mounted_agents = metadata.get("mounted_agents")
        if isinstance(mounted_agents, int) and mounted_agents > 1:
            add_issue(
                issues,
                "warning",
                "STRUCTURED_SUBAGENTS_UNAVAILABLE",
                row_number,
                "metadata.mounted_agents",
                "observed count is retained but no subagent assets are fabricated",
            )

        for position, requirement in enumerate(metadata.get("network_requirements") or []):
            network_requirement_count += 1
            port = requirement.get("port")
            try:
                parsed_port = int(port)
                if not 1 <= parsed_port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                add_issue(
                    issues,
                    "error",
                    "NETWORK_PORT_INVALID",
                    row_number,
                    f"metadata.network_requirements[{position}].port",
                    "port must be between 1 and 65535",
                )

    flattened_ids = [object_id for values in object_ids.values() for object_id in values]
    issue_counts = Counter(item["code"] for item in issues)
    error_count = sum(item["severity"] == "error" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    mounted_distribution = Counter(
        item.get("metadata", {}).get("mounted_agents") for item in gateways
    )

    samples = []
    for position, gateway in enumerate(gateways[:3]):
        samples.append(
            {
                "source_gateway_id": gateway.get("gateway_id"),
                "source_server_id": gateway.get("metadata", {}).get("server_id"),
                "server_id": object_ids["server"][position],
                "runtime_id": object_ids["runtime"][position],
                "agent_id": object_ids["agent"][position],
                "deployment_id": object_ids["deployment"][position],
                "snapshot_id": object_ids["snapshot"][position],
            }
        )

    return {
        "ready_to_commit": error_count == 0 and warning_count == 0,
        "structurally_mappable": error_count == 0,
        "source_hash": canonical_hash(source),
        "source_profile": {
            "rows": len(gateways),
            "environment_code": defaults.get("env"),
            "regions": dict(sorted(Counter([defaults.get("region")]).items())),
            "deployment_modes": dict(sorted(Counter(item.get("deployment_mode") for item in gateways).items())),
            "runtime_families": dict(sorted(Counter(item.get("metadata", {}).get("agent_type") for item in gateways).items())),
            "runtime_versions": dict(sorted(Counter(item.get("metadata", {}).get("openclaw_version") for item in gateways).items())),
            "mounted_agent_counts": {str(key): value for key, value in sorted(mounted_distribution.items())},
            "unique_department_names": len({item.get("department") for item in gateways}),
            "network_requirements": network_requirement_count,
        },
        "staged_counts": {
            "environments": 1 if gateways else 0,
            "cloud_providers": 1 if gateways else 0,
            "servers": len(object_ids["server"]),
            "runtimes": len(object_ids["runtime"]),
            "primary_agents": len(object_ids["agent"]),
            "subagents": 0,
            "agent_deployments": len(object_ids["deployment"]),
            "agent_deployment_members": len(object_ids["member"]),
            "runtime_discovery_snapshots": len(object_ids["snapshot"]),
            "external_identifiers": external_identifier_count,
            "network_requirement_candidates": network_requirement_count,
            "management_groups": 0,
        },
        "identity_checks": {
            "all_generated_ids_are_unique": len(flattened_ids) == len(set(flattened_ids)),
            "generated_id_count": len(flattened_ids),
            "raw_payload_hash_count": len(raw_hashes),
            "unique_raw_payload_hash_count": len(set(raw_hashes)),
        },
        "unresolved": {
            "service_archetypes": unresolved_service_archetypes,
            "department_codes": unresolved_department_codes,
            "administrator_employee_numbers": unresolved_admin_references,
            "runtime_primary_agent_keys": unresolved_main_keys,
        },
        "issue_summary": {
            "errors": error_count,
            "warnings": warning_count,
            "by_code": dict(sorted(issue_counts.items())),
        },
        "samples": samples,
    }


def main():
    parser = argparse.ArgumentParser(description="Dry-run the legacy lobster inventory migration without writes.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.input.read_text())
    report = build_migration_plan(source)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)
    return 0 if report["structurally_mappable"] else 1


if __name__ == "__main__":
    sys.exit(main())
