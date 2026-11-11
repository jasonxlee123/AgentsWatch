#!/usr/bin/env python3
import argparse
import ipaddress
import json
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "tests" / "fixtures" / "core_assets_valid.json"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    path: str
    message: str


COLLECTION_IDS = {
    "environments": "environment_id",
    "cloud_providers": "cloud_provider_id",
    "departments": "department_id",
    "people": "person_id",
    "runtime_families": "runtime_family_id",
    "servers": "server_id",
    "management_groups": "group_id",
    "server_group_members": "member_id",
    "runtimes": "runtime_id",
    "agents": "agent_id",
    "agent_access_users": "access_id",
    "agent_group_members": "member_id",
    "agent_deployments": "deployment_id",
    "agent_deployment_members": "deployment_member_id",
    "agent_network_requirements": "network_requirement_id",
}

ENUM_FIELDS = {
    ("environments", "lifecycle_status"): {"planned", "active", "disabled", "retired"},
    ("cloud_providers", "status"): {"active", "disabled", "retired"},
    ("runtime_families", "status"): {"active", "disabled", "retired"},
    ("servers", "lifecycle_status"): {"planned", "active", "maintenance", "disabled", "retired"},
    ("management_groups", "resource_type"): {"server", "agent"},
    ("management_groups", "lifecycle_status"): {"active", "disabled", "retired"},
    ("runtimes", "runtime_role"): {"gateway"},
    ("runtimes", "deployment_mode"): {"baremetal", "container"},
    ("runtimes", "lifecycle_status"): {"planned", "active", "disabled", "retired"},
    ("runtimes", "observed_status"): {"unknown", "running", "stopped", "degraded", "unreachable"},
    ("agents", "structure_role"): {"primary", "subagent"},
    ("agents", "service_archetype"): {"personal", "department", "business"},
    ("agents", "lifecycle_status"): {"requested", "testing", "active", "suspended", "retired"},
    ("agent_deployments", "lifecycle_status"): {"planned", "active", "inactive", "retired"},
    ("agent_deployment_members", "mapping_status"): {"planned", "verified", "unresolved"},
    ("agent_network_requirements", "target_type"): {"dns", "ipv4", "ipv6", "cidr", "url"},
    ("agent_network_requirements", "protocol"): {"tcp", "udp", "http", "https"},
}

VERSIONED_COLLECTIONS = {
    "environments",
    "cloud_providers",
    "departments",
    "people",
    "runtime_families",
    "servers",
    "management_groups",
    "runtimes",
    "agents",
    "agent_deployments",
}


def issue(issues, severity, code, path, message):
    issues.append(ValidationIssue(severity, code, path, message))


def build_indexes(payload, issues):
    indexes = {}
    all_ids = {}
    for collection, id_field in COLLECTION_IDS.items():
        items = payload.get(collection, [])
        if not isinstance(items, list):
            issue(issues, "error", "COLLECTION_NOT_LIST", collection, "collection must be a list")
            items = []
        index = {}
        for position, item in enumerate(items):
            path = f"{collection}[{position}]"
            if not isinstance(item, dict):
                issue(issues, "error", "ITEM_NOT_OBJECT", path, "item must be an object")
                continue
            object_id = item.get(id_field)
            if not object_id:
                issue(issues, "error", "ID_REQUIRED", path, f"{id_field} is required")
                continue
            try:
                uuid.UUID(str(object_id))
            except ValueError:
                issue(issues, "error", "ID_INVALID", f"{path}.{id_field}", "value must be a UUID")
            if object_id in index:
                issue(issues, "error", "ID_DUPLICATE", f"{path}.{id_field}", "ID is duplicated")
            else:
                index[object_id] = item
            if object_id in all_ids:
                issue(
                    issues,
                    "error",
                    "ID_REUSED_ACROSS_TYPES",
                    f"{path}.{id_field}",
                    f"ID is already used by {all_ids[object_id]}",
                )
            else:
                all_ids[object_id] = collection
        indexes[collection] = index
    return indexes


def validate_unique(items, field, collection, issues):
    seen = {}
    for position, item in enumerate(items):
        value = item.get(field)
        if value in (None, ""):
            continue
        path = f"{collection}[{position}].{field}"
        if value in seen:
            issue(issues, "error", "VALUE_DUPLICATE", path, f"value duplicates {seen[value]}")
        else:
            seen[value] = path


def validate_enums_and_versions(payload, issues):
    for (collection, field), allowed in ENUM_FIELDS.items():
        for position, item in enumerate(payload.get(collection, [])):
            value = item.get(field)
            if value not in allowed:
                issue(
                    issues,
                    "error",
                    "ENUM_VALUE_INVALID",
                    f"{collection}[{position}].{field}",
                    f"value must be one of {sorted(allowed)}",
                )
    for collection in VERSIONED_COLLECTIONS:
        for position, item in enumerate(payload.get(collection, [])):
            value = item.get("row_version")
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                issue(
                    issues,
                    "error",
                    "ROW_VERSION_INVALID",
                    f"{collection}[{position}].row_version",
                    "row_version must be an integer greater than or equal to 1",
                )


def validate_organization(payload, indexes, issues):
    for position, department in enumerate(payload.get("departments", [])):
        require_ref(
            department,
            "parent_department_id",
            indexes["departments"],
            f"departments[{position}]",
            issues,
            required=False,
        )
    for position, person in enumerate(payload.get("people", [])):
        require_ref(
            person,
            "primary_department_id",
            indexes["departments"],
            f"people[{position}]",
            issues,
            required=False,
        )


def require_ref(item, field, target_index, path, issues, required=True):
    value = item.get(field)
    if value in (None, ""):
        if required:
            issue(issues, "error", "REFERENCE_REQUIRED", f"{path}.{field}", "reference is required")
        return None
    target = target_index.get(value)
    if target is None:
        issue(issues, "error", "REFERENCE_NOT_FOUND", f"{path}.{field}", "referenced object does not exist")
    return target


def validate_servers(payload, indexes, issues):
    seen_ips = {}
    seen_provider_ids = {}
    for position, server in enumerate(payload.get("servers", [])):
        path = f"servers[{position}]"
        require_ref(server, "environment_id", indexes["environments"], path, issues)
        require_ref(server, "cloud_provider_id", indexes["cloud_providers"], path, issues, required=False)
        provider_key = (server.get("cloud_provider_id"), server.get("provider_instance_id"))
        if all(provider_key):
            if provider_key in seen_provider_ids:
                issue(
                    issues,
                    "error",
                    "SERVER_PROVIDER_ID_DUPLICATE",
                    f"{path}.provider_instance_id",
                    f"provider instance ID duplicates {seen_provider_ids[provider_key]}",
                )
            else:
                seen_provider_ids[provider_key] = f"{path}.provider_instance_id"
        for field in ("cpu_cores", "memory_gib", "disk_gib"):
            value = server.get(field)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
                issue(issues, "error", "SERVER_CAPACITY_INVALID", f"{path}.{field}", "value must be positive")
        for field in ("internal_ip", "public_ip"):
            value = server.get(field)
            if not value:
                continue
            try:
                normalized = str(ipaddress.ip_address(value))
            except ValueError:
                issue(issues, "error", "SERVER_IP_INVALID", f"{path}.{field}", "value must be an IP address")
                continue
            key = (server.get("environment_id"), field, normalized)
            if key in seen_ips:
                issue(issues, "error", "SERVER_IP_DUPLICATE", f"{path}.{field}", f"IP duplicates {seen_ips[key]}")
            else:
                seen_ips[key] = f"{path}.{field}"
        account = str(server.get("work_account") or "")
        if any(marker in account.lower() for marker in ("password=", "token=", "private key")):
            issue(issues, "error", "SERVER_ACCOUNT_CONTAINS_SECRET", f"{path}.work_account", "only a username is allowed")


def validate_runtimes(payload, indexes, issues):
    allowed_modes = {"baremetal", "container"}
    allowed_roles = {"gateway"}
    for position, runtime in enumerate(payload.get("runtimes", [])):
        path = f"runtimes[{position}]"
        server = require_ref(runtime, "server_id", indexes["servers"], path, issues)
        require_ref(runtime, "runtime_family_id", indexes["runtime_families"], path, issues)
        if server and server.get("lifecycle_status") == "retired":
            issue(issues, "error", "RUNTIME_ON_RETIRED_SERVER", f"{path}.server_id", "runtime cannot use a retired server")
        if (
            server
            and runtime.get("lifecycle_status") == "active"
            and server.get("lifecycle_status") not in {"active", "maintenance"}
        ):
            issue(
                issues,
                "error",
                "ACTIVE_RUNTIME_SERVER_STATE_INVALID",
                f"{path}.server_id",
                "active runtime requires an active or maintenance server",
            )
        if runtime.get("runtime_role") not in allowed_roles:
            issue(issues, "error", "RUNTIME_ROLE_INVALID", f"{path}.runtime_role", "only gateway is supported")
        mode = runtime.get("deployment_mode")
        if mode not in allowed_modes:
            issue(issues, "error", "RUNTIME_MODE_INVALID", f"{path}.deployment_mode", "unsupported deployment mode")
        if mode == "baremetal" and (runtime.get("container_name") or runtime.get("image_ref")):
            issue(issues, "error", "RUNTIME_MODE_FIELDS_INVALID", path, "baremetal runtime cannot have container fields")
        if mode == "container" and (not runtime.get("container_name") or not runtime.get("image_ref")):
            issue(issues, "error", "RUNTIME_MODE_FIELDS_INVALID", path, "container runtime requires container_name and image_ref")
        port = runtime.get("endpoint_port")
        if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
            issue(issues, "error", "RUNTIME_PORT_INVALID", f"{path}.endpoint_port", "port must be between 1 and 65535")


def validate_agents(payload, indexes, issues):
    service_fields = ("primary_service_person_id", "primary_service_department_id", "business_domain", "business_scenario")
    for position, agent in enumerate(payload.get("agents", [])):
        path = f"agents[{position}]"
        role = agent.get("structure_role")
        archetype = agent.get("service_archetype")
        require_ref(agent, "owning_department_id", indexes["departments"], path, issues, required=False)
        require_ref(agent, "business_admin_person_id", indexes["people"], path, issues, required=False)
        require_ref(agent, "it_admin_person_id", indexes["people"], path, issues, required=False)
        require_ref(agent, "primary_service_person_id", indexes["people"], path, issues, required=False)
        require_ref(agent, "primary_service_department_id", indexes["departments"], path, issues, required=False)

        if role == "primary":
            if agent.get("parent_agent_id"):
                issue(issues, "error", "PRIMARY_AGENT_HAS_PARENT", f"{path}.parent_agent_id", "primary agent cannot have a parent")
            if agent.get("lifecycle_status") in {"testing", "active"}:
                for field in ("business_admin_person_id", "it_admin_person_id"):
                    if not agent.get(field):
                        issue(issues, "error", "ACTIVE_AGENT_ADMIN_REQUIRED", f"{path}.{field}", "testing/active primary agent requires this administrator")
            if archetype == "personal":
                if not agent.get("primary_service_person_id"):
                    issue(issues, "error", "PERSONAL_AGENT_PERSON_REQUIRED", path, "personal agent requires one primary service person")
                for field in ("primary_service_department_id", "business_domain", "business_scenario"):
                    if agent.get(field):
                        issue(issues, "error", "INVALID_SERVICE_ARCHETYPE_FIELDS", f"{path}.{field}", "field is not allowed for personal agent")
            elif archetype == "department":
                if not agent.get("primary_service_department_id"):
                    issue(issues, "error", "DEPARTMENT_AGENT_DEPARTMENT_REQUIRED", path, "department agent requires one primary service department")
                for field in ("primary_service_person_id", "business_domain", "business_scenario"):
                    if agent.get(field):
                        issue(issues, "error", "INVALID_SERVICE_ARCHETYPE_FIELDS", f"{path}.{field}", "field is not allowed for department agent")
            elif archetype == "business":
                if not agent.get("business_domain") or not agent.get("business_scenario"):
                    issue(issues, "error", "BUSINESS_AGENT_SCENARIO_REQUIRED", path, "business agent requires domain and scenario")
                for field in ("primary_service_person_id", "primary_service_department_id"):
                    if agent.get(field):
                        issue(issues, "error", "INVALID_SERVICE_ARCHETYPE_FIELDS", f"{path}.{field}", "field is not allowed for business agent")
            else:
                issue(issues, "error", "AGENT_ARCHETYPE_INVALID", f"{path}.service_archetype", "unsupported service archetype")
        elif role == "subagent":
            parent = require_ref(agent, "parent_agent_id", indexes["agents"], path, issues)
            if parent:
                if parent.get("structure_role") != "primary":
                    issue(issues, "error", "INVALID_AGENT_HIERARCHY", f"{path}.parent_agent_id", "subagent parent must be primary")
                if parent.get("service_archetype") != archetype:
                    issue(issues, "error", "SUBAGENT_ARCHETYPE_MISMATCH", f"{path}.service_archetype", "subagent must inherit parent archetype")
                if agent.get("lifecycle_status") == "active" and parent.get("lifecycle_status") not in {"testing", "active"}:
                    issue(
                        issues,
                        "error",
                        "ACTIVE_SUBAGENT_PARENT_STATE_INVALID",
                        f"{path}.parent_agent_id",
                        "active subagent requires a testing or active parent",
                    )
            inherited_fields = (*service_fields, "business_admin_person_id", "it_admin_person_id")
            for field in inherited_fields:
                if agent.get(field):
                    issue(issues, "error", "SUBAGENT_FIELD_MUST_INHERIT", f"{path}.{field}", "subagent must inherit this field")
        else:
            issue(issues, "error", "AGENT_STRUCTURE_ROLE_INVALID", f"{path}.structure_role", "unsupported structure role")


def validate_access_and_groups(payload, indexes, issues):
    active_access = set()
    for position, access in enumerate(payload.get("agent_access_users", [])):
        path = f"agent_access_users[{position}]"
        agent = require_ref(access, "agent_id", indexes["agents"], path, issues)
        require_ref(access, "person_id", indexes["people"], path, issues)
        if agent and agent.get("structure_role") != "primary":
            issue(issues, "error", "SUBAGENT_ACCESS_GRANT", f"{path}.agent_id", "subagent cannot have direct access users")
        if access.get("active", True):
            key = (access.get("agent_id"), access.get("person_id"))
            if key in active_access:
                issue(issues, "error", "ACCESS_GRANT_DUPLICATE", path, "active access grant is duplicated")
            active_access.add(key)

    active_members = set()
    for collection, target_field, expected_type in (
        ("server_group_members", "server_id", "server"),
        ("agent_group_members", "agent_id", "agent"),
    ):
        target_index = indexes["servers"] if expected_type == "server" else indexes["agents"]
        for position, member in enumerate(payload.get(collection, [])):
            path = f"{collection}[{position}]"
            group = require_ref(member, "group_id", indexes["management_groups"], path, issues)
            target = require_ref(member, target_field, target_index, path, issues)
            if group and group.get("resource_type") != expected_type:
                issue(issues, "error", "GROUP_RESOURCE_TYPE_MISMATCH", f"{path}.group_id", "group resource type does not match member")
            if expected_type == "agent" and target and target.get("structure_role") != "primary":
                issue(issues, "error", "SUBAGENT_GROUP_MEMBERSHIP", f"{path}.agent_id", "subagent cannot join a management group")
            if member.get("active", True):
                key = (collection, member.get("group_id"), member.get(target_field))
                if key in active_members:
                    issue(issues, "error", "GROUP_MEMBERSHIP_DUPLICATE", path, "active group membership is duplicated")
                active_members.add(key)


def validate_deployments(payload, indexes, issues):
    active_by_agent = {}
    active_by_runtime = {}
    deployment_by_id = indexes["agent_deployments"]
    for position, deployment in enumerate(payload.get("agent_deployments", [])):
        path = f"agent_deployments[{position}]"
        agent = require_ref(deployment, "primary_agent_id", indexes["agents"], path, issues)
        runtime = require_ref(deployment, "runtime_id", indexes["runtimes"], path, issues)
        if agent and agent.get("structure_role") != "primary":
            issue(issues, "error", "DEPLOYMENT_AGENT_NOT_PRIMARY", f"{path}.primary_agent_id", "deployment must target a primary agent")
        if deployment.get("lifecycle_status") == "active":
            if agent and agent.get("lifecycle_status") not in {"testing", "active"}:
                issue(
                    issues,
                    "error",
                    "ACTIVE_DEPLOYMENT_AGENT_STATE_INVALID",
                    f"{path}.primary_agent_id",
                    "active deployment requires a testing or active primary agent",
                )
            if runtime and runtime.get("lifecycle_status") != "active":
                issue(
                    issues,
                    "error",
                    "ACTIVE_DEPLOYMENT_RUNTIME_STATE_INVALID",
                    f"{path}.runtime_id",
                    "active deployment requires an active runtime",
                )
            agent_id = deployment.get("primary_agent_id")
            if agent_id in active_by_agent:
                issue(issues, "error", "ACTIVE_AGENT_DEPLOYMENT_CONFLICT", path, f"agent already has active deployment {active_by_agent[agent_id]}")
            else:
                active_by_agent[agent_id] = deployment.get("deployment_id")
            runtime_id = deployment.get("runtime_id")
            if runtime_id in active_by_runtime:
                issue(
                    issues,
                    "error",
                    "ACTIVE_RUNTIME_DEPLOYMENT_CONFLICT",
                    path,
                    f"Runtime already has active primary deployment {active_by_runtime[runtime_id]}",
                )
            else:
                active_by_runtime[runtime_id] = deployment.get("deployment_id")

    members_by_deployment = defaultdict(list)
    for position, member in enumerate(payload.get("agent_deployment_members", [])):
        path = f"agent_deployment_members[{position}]"
        deployment = require_ref(member, "deployment_id", deployment_by_id, path, issues)
        runtime = require_ref(member, "runtime_id", indexes["runtimes"], path, issues)
        agent = require_ref(member, "agent_id", indexes["agents"], path, issues)
        if deployment and runtime and member.get("runtime_id") != deployment.get("runtime_id"):
            issue(
                issues,
                "error",
                "DEPLOYMENT_MEMBER_RUNTIME_MISMATCH",
                f"{path}.runtime_id",
                "member runtime must match its deployment runtime",
            )
        if agent and member.get("member_role") != agent.get("structure_role"):
            issue(issues, "error", "DEPLOYMENT_MEMBER_ROLE_MISMATCH", f"{path}.member_role", "member role must match agent structure role")
        status = member.get("mapping_status")
        if status not in {"planned", "verified", "unresolved"}:
            issue(issues, "error", "DEPLOYMENT_MAPPING_STATUS_INVALID", f"{path}.mapping_status", "unsupported mapping status")
        if status == "verified" and not member.get("runtime_agent_key"):
            issue(issues, "error", "RUNTIME_AGENT_KEY_REQUIRED", f"{path}.runtime_agent_key", "verified mapping requires a runtime key")
        if status == "unresolved":
            issue(issues, "warning", "RUNTIME_AGENT_KEY_UNRESOLVED", path, "runtime agent key remains unresolved")
        if deployment:
            members_by_deployment[deployment["deployment_id"]].append((member, path))

    active_runtime_keys = {}
    for deployment_id, deployment in deployment_by_id.items():
        members = members_by_deployment.get(deployment_id, [])
        primaries = [(member, path) for member, path in members if member.get("member_role") == "primary"]
        if len(primaries) != 1:
            issue(issues, "error", "DEPLOYMENT_PRIMARY_MEMBER_INVALID", f"agent_deployments[{deployment_id}]", "deployment must contain exactly one primary member")
            continue
        primary_member, primary_path = primaries[0]
        if primary_member.get("agent_id") != deployment.get("primary_agent_id"):
            issue(issues, "error", "DEPLOYMENT_PRIMARY_MEMBER_INVALID", primary_path, "primary member does not match deployment primary agent")
        primary_member_id = primary_member.get("deployment_member_id")
        for member, path in members:
            if member.get("member_role") != "subagent":
                continue
            agent = indexes["agents"].get(member.get("agent_id"))
            if member.get("parent_member_id") != primary_member_id:
                issue(issues, "error", "DEPLOYMENT_SUBAGENT_PARENT_INVALID", f"{path}.parent_member_id", "subagent member must point to deployment primary member")
            if agent and agent.get("parent_agent_id") != deployment.get("primary_agent_id"):
                issue(issues, "error", "DEPLOYMENT_SUBAGENT_PARENT_INVALID", f"{path}.agent_id", "subagent does not belong to deployment primary agent")
        if deployment.get("lifecycle_status") == "active":
            for member, path in members:
                key_value = member.get("runtime_agent_key")
                if not key_value:
                    continue
                key = (member.get("runtime_id"), key_value)
                if key in active_runtime_keys:
                    issue(issues, "error", "RUNTIME_AGENT_KEY_DUPLICATE", f"{path}.runtime_agent_key", f"key duplicates {active_runtime_keys[key]}")
                else:
                    active_runtime_keys[key] = f"{path}.runtime_agent_key"


def validate_network_requirements(payload, indexes, issues):
    for position, requirement in enumerate(payload.get("agent_network_requirements", [])):
        path = f"agent_network_requirements[{position}]"
        require_ref(requirement, "agent_id", indexes["agents"], path, issues)
        start = requirement.get("port_from")
        end = requirement.get("port_to")
        if start is not None and (not isinstance(start, int) or not 1 <= start <= 65535):
            issue(issues, "error", "NETWORK_PORT_INVALID", f"{path}.port_from", "port must be between 1 and 65535")
        if end is not None and (not isinstance(end, int) or not 1 <= end <= 65535 or (start is not None and end < start)):
            issue(issues, "error", "NETWORK_PORT_INVALID", f"{path}.port_to", "port_to must be valid and not lower than port_from")


def model_summary(payload):
    deployments = payload.get("agent_deployments", [])
    runtime_load = Counter(item.get("runtime_id") for item in deployments if item.get("lifecycle_status") == "active")
    server_memberships = Counter(item.get("server_id") for item in payload.get("server_group_members", []) if item.get("active", True))
    agent_memberships = Counter(item.get("agent_id") for item in payload.get("agent_group_members", []) if item.get("active", True))
    return {
        "counts": {collection: len(payload.get(collection, [])) for collection in COLLECTION_IDS},
        "shared_runtime_count": sum(1 for count in runtime_load.values() if count > 1),
        "max_primary_agents_per_runtime": max(runtime_load.values(), default=0),
        "servers_in_multiple_groups": sum(1 for count in server_memberships.values() if count > 1),
        "agents_in_multiple_groups": sum(1 for count in agent_memberships.values() if count > 1),
    }


def validate_payload(payload):
    issues = []
    indexes = build_indexes(payload, issues)
    validate_enums_and_versions(payload, issues)
    validate_unique(payload.get("environments", []), "environment_code", "environments", issues)
    validate_unique(payload.get("cloud_providers", []), "provider_code", "cloud_providers", issues)
    validate_unique(payload.get("departments", []), "department_code", "departments", issues)
    validate_unique(payload.get("people", []), "employee_no", "people", issues)
    validate_unique(payload.get("runtime_families", []), "family_code", "runtime_families", issues)
    validate_unique(payload.get("management_groups", []), "group_code", "management_groups", issues)
    validate_organization(payload, indexes, issues)
    validate_servers(payload, indexes, issues)
    validate_runtimes(payload, indexes, issues)
    validate_agents(payload, indexes, issues)
    validate_access_and_groups(payload, indexes, issues)
    validate_deployments(payload, indexes, issues)
    validate_network_requirements(payload, indexes, issues)
    errors = sum(1 for item in issues if item.severity == "error")
    warnings = sum(1 for item in issues if item.severity == "warning")
    return {
        "ok": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": model_summary(payload),
        "issues": [asdict(item) for item in issues],
    }


def main():
    parser = argparse.ArgumentParser(description="Validate simulated core asset relationships without a database.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    report = validate_payload(payload)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
