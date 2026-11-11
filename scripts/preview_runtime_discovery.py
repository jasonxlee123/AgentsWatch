#!/usr/bin/env python3
import argparse
import copy
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path


SECRET_KEY = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|private[_-]?key|cookie|authorization)")
SECRET_VALUE = re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[a-z0-9_-]{12,})")
SNAPSHOT_NAMESPACE = uuid.UUID("e808fa95-77cf-4da6-a049-544f53261c72")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def redact(value, key=None):
    if key is not None and SECRET_KEY.search(str(key)):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and SECRET_VALUE.search(value):
        return "[REDACTED]"
    return value


def validate_timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("discovered_at must include a timezone")


def normalize_agents(agents):
    keys = set()
    normalized = []
    for position, item in enumerate(agents):
        key = str(item.get("key") or "").strip()
        role = item.get("role")
        parent_key = item.get("parent_key")
        if not key:
            raise ValueError(f"agents[{position}].key is required")
        if key in keys:
            raise ValueError(f"duplicate runtime agent key {key!r}")
        if role not in {"primary", "subagent"}:
            raise ValueError(f"agents[{position}].role must be primary or subagent")
        if role == "primary" and parent_key is not None:
            raise ValueError(f"primary runtime agent {key!r} cannot have parent_key")
        if role == "subagent" and not parent_key:
            raise ValueError(f"subagent runtime agent {key!r} requires parent_key")
        keys.add(key)
        normalized.append(
            {
                "runtime_agent_key": key,
                "name": str(item.get("name") or key).strip(),
                "member_role": role,
                "parent_runtime_agent_key": parent_key,
                "model": item.get("model"),
                "skills": sorted(set(item.get("skills") or [])),
                "tools": sorted(set(item.get("tools") or [])),
                "mapping_status": "unresolved",
            }
        )
    by_key = {item["runtime_agent_key"]: item for item in normalized}
    for item in normalized:
        parent_key = item["parent_runtime_agent_key"]
        if not parent_key:
            continue
        parent = by_key.get(parent_key)
        if parent is None:
            raise ValueError(f"parent runtime agent key {parent_key!r} does not exist")
        if parent["member_role"] != "primary":
            raise ValueError(f"parent runtime agent key {parent_key!r} is not primary")
    return normalized


def preview_discovery(payload, previous_content_hash=None):
    runtime_id = str(payload.get("runtime_id") or "")
    uuid.UUID(runtime_id)
    family = str(payload.get("runtime_family_code") or "").strip().lower()
    if family not in {"openclaw", "hermes"}:
        raise ValueError("runtime_family_code must be openclaw or hermes in this prototype")
    discovered_at = payload.get("discovered_at")
    validate_timestamp(discovered_at)
    status = payload.get("discovery_status")
    if status not in {"succeeded", "partial", "failed"}:
        raise ValueError("discovery_status must be succeeded, partial or failed")
    normalized_agents = normalize_agents(payload.get("agents") or [])
    redacted_payload = redact(copy.deepcopy(payload))
    content = {
        "runtime_family_code": family,
        "actual_version": payload.get("actual_version"),
        "extensions": redact(payload.get("extensions") or []),
        "config": redact(payload.get("config") or {}),
        "agents": normalized_agents,
    }
    content_hash = hashlib.sha256(canonical_json(content).encode()).hexdigest()
    should_create = previous_content_hash != content_hash
    snapshot_id = None
    if should_create:
        snapshot_id = str(
            uuid.uuid5(
                SNAPSHOT_NAMESPACE,
                f"{runtime_id}:{content_hash}:{discovered_at}",
            )
        )
    return {
        "runtime_id": runtime_id,
        "runtime_family_code": family,
        "content_hash": content_hash,
        "previous_content_hash": previous_content_hash,
        "should_create_snapshot": should_create,
        "snapshot": None
        if not should_create
        else {
            "snapshot_id": snapshot_id,
            "runtime_id": runtime_id,
            "discovered_at": discovered_at,
            "discovery_status": status,
            "discovery_source": "local_fixture",
            "actual_version": payload.get("actual_version"),
            "observed_agent_count": len(normalized_agents),
            "agent_count_confidence": "verified",
            "content_hash": content_hash,
            "extension_summary": content["extensions"],
            "config_summary": content["config"],
            "raw_payload": redacted_payload,
        },
        "agent_projection": normalized_agents,
        "summary": {
            "primary_agents": sum(item["member_role"] == "primary" for item in normalized_agents),
            "subagents": sum(item["member_role"] == "subagent" for item in normalized_agents),
            "unresolved_mappings": len(normalized_agents),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Normalize a local simulated Runtime discovery response.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--previous-content-hash")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    try:
        report = preview_discovery(payload, args.previous_content_hash)
    except (ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    rendered = json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
