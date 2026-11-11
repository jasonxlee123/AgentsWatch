#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


LABEL_KEYS = (
    "cluster",
    "gateway_id",
    "runtime_id",
    "server_id",
    "primary_agent_id",
    "agent_deployment_id",
    "server_display_id",
    "env",
    "region",
    "team",
    "deployment_mode",
    "service_mode",
    "lobster_name",
    "department",
    "native_scrape_expected",
)
DIAGNOSTICS_PLUGINS = ("diagnostics-otel", "diagnostics-prometheus")
GATEWAY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GATEWAY_BIND_MODES = {"auto", "loopback", "lan", "tailnet", "custom"}
OTEL_HEADER_FIELDS = {
    "x-openclaw-gateway-id": "gateway_id",
    "x-agentswatch-runtime-id": "runtime_id",
    "x-agentswatch-server-id": "server_id",
    "x-agentswatch-primary-agent-id": "primary_agent_id",
    "x-agentswatch-agent-deployment-id": "agent_deployment_id",
}


def merged(defaults: dict, gateway: dict) -> dict:
    item = {**defaults, **gateway}
    metadata = item.get("metadata") or {}
    if not item.get("server_display_id") and metadata.get("server_id"):
        item["server_display_id"] = metadata["server_id"]
    if not item.get("gateway_id"):
        raise ValueError(f"gateway is missing gateway_id: {gateway}")
    if not GATEWAY_ID_PATTERN.fullmatch(str(item["gateway_id"])):
        raise ValueError(f"gateway_id contains unsupported characters: {item['gateway_id']}")
    if not item.get("host"):
        raise ValueError(f"gateway {item['gateway_id']} is missing host")
    if not item.get("otel_endpoint"):
        raise ValueError(f"gateway {item['gateway_id']} is missing otel_endpoint")
    parsed_endpoint = urlsplit(str(item["otel_endpoint"]))
    if parsed_endpoint.scheme not in ("http", "https") or not parsed_endpoint.netloc:
        raise ValueError(f"gateway {item['gateway_id']} has invalid OTLP/HTTP endpoint: {item['otel_endpoint']}")
    sample_rate = float(item.get("sample_rate", 1.0))
    if not 0 <= sample_rate <= 1:
        raise ValueError(f"gateway {item['gateway_id']} sample_rate must be between 0 and 1")
    flush_interval_ms = int(item.get("flush_interval_ms", 60000))
    if flush_interval_ms < 1000:
        raise ValueError(f"gateway {item['gateway_id']} flush_interval_ms must be at least 1000")
    stuck_session_warn_ms = int(item.get("stuck_session_warn_ms", 120000))
    stuck_session_abort_ms = int(item.get("stuck_session_abort_ms", 360000))
    if stuck_session_warn_ms < 1000:
        raise ValueError(f"gateway {item['gateway_id']} stuck_session_warn_ms must be at least 1000")
    if stuck_session_abort_ms < stuck_session_warn_ms:
        raise ValueError(f"gateway {item['gateway_id']} stuck_session_abort_ms must not be lower than stuck_session_warn_ms")
    gateway_bind = item.get("gateway_bind")
    if gateway_bind is not None and str(gateway_bind) not in GATEWAY_BIND_MODES:
        raise ValueError(
            f"gateway {item['gateway_id']} gateway_bind must be one of {sorted(GATEWAY_BIND_MODES)}"
        )
    native_scrape_expected = item.get("native_scrape_expected")
    if native_scrape_expected is None:
        native_scrape_expected = gateway_bind in {"lan", "tailnet", "custom"}
    if not isinstance(native_scrape_expected, bool):
        raise ValueError(f"gateway {item['gateway_id']} native_scrape_expected must be a boolean")
    item["native_scrape_expected"] = "true" if native_scrape_expected else "false"
    return item


def is_enabled(item: dict) -> bool:
    return item.get("enabled", True) is not False


def yaml_scalar(value: object) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_scrape_configs(items: list[dict]) -> str:
    if not items:
        return "scrape_configs: []\n"
    lines: list[str] = ["scrape_configs:"]
    for item in items:
        gateway_id = item["gateway_id"]
        target = f"{item['host']}:{item.get('port', 16402)}"
        lines.append(f"  - job_name: {yaml_scalar(f'openclaw-gateway-{gateway_id}')}")
        lines.append("    scrape_interval: 30s")
        lines.append("    scrape_timeout: 10s")
        lines.append(f"    metrics_path: {yaml_scalar(item.get('metrics_path', '/api/diagnostics/prometheus'))}")
        lines.append(f"    scheme: {yaml_scalar(item.get('scheme', 'http'))}")
        lines.append("    authorization:")
        lines.append(f"      credentials_file: {yaml_scalar(f'/etc/prometheus/secrets/gateways/{gateway_id}.token')}")
        lines.append("    static_configs:")
        lines.append("      - targets:")
        lines.append(f"          - {yaml_scalar(target)}")
        lines.append("        labels:")
        for key in LABEL_KEYS:
            value = item.get(key, "")
            if key == "native_scrape_expected" and value == "":
                value = "true" if item.get("gateway_bind") in {"lan", "tailnet", "custom"} else "false"
            if value != "":
                lines.append(f"          {key}: {yaml_scalar(value)}")
        lines.append("    relabel_configs:")
        lines.append("      - target_label: job")
        lines.append('        replacement: "openclaw-gateways"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def endpoint(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def otel_headers(item: dict) -> dict[str, str]:
    headers = {}
    for header, field in OTEL_HEADER_FIELDS.items():
        value = str(item.get(field, "")).strip()
        if not value:
            continue
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"gateway {item['gateway_id']} field {field} must be ASCII for OTLP headers") from exc
        headers[header] = value
    return headers


def render_plugins(item: dict) -> dict:
    plugins = {
        "entries": {
            plugin: {"enabled": True}
            for plugin in DIAGNOSTICS_PLUGINS
        }
    }
    if "plugins_allow" in item:
        plugins["allow"] = list(dict.fromkeys([*item["plugins_allow"], *DIAGNOSTICS_PLUGINS]))
    return plugins


def render_otel_patch(item: dict) -> str:
    otel_base = item["otel_endpoint"]
    service_name = item.get("service_name", "openclaw-gateway")
    payload = {
        "plugins": render_plugins(item),
        "diagnostics": {
            "enabled": True,
            "flags": [],
            "stuckSessionWarnMs": int(item.get("stuck_session_warn_ms", 120000)),
            "stuckSessionAbortMs": int(item.get("stuck_session_abort_ms", 360000)),
            "memoryPressureSnapshot": False,
            "otel": {
                "enabled": True,
                "endpoint": otel_base,
                "tracesEndpoint": endpoint(otel_base, "/v1/traces"),
                "metricsEndpoint": endpoint(otel_base, "/v1/metrics"),
                "logsEndpoint": endpoint(otel_base, "/v1/logs"),
                "protocol": "http/protobuf",
                "serviceName": service_name,
                "headers": otel_headers(item),
                "traces": True,
                "metrics": True,
                "logs": True,
                "logsExporter": "otlp",
                "sampleRate": float(item.get("sample_rate", 1.0)),
                "flushIntervalMs": int(item.get("flush_interval_ms", 60000)),
                "captureContent": {
                    "enabled": False,
                    "inputMessages": False,
                    "outputMessages": False,
                    "toolInputs": False,
                    "toolOutputs": False,
                    "systemPrompt": False,
                    "toolDefinitions": False,
                },
            },
        }
    }
    if item.get("gateway_bind"):
        payload["gateway"] = {"bind": item["gateway_bind"]}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-gateway Prometheus scrape configs and OpenClaw diagnostics patches from lobster inventory.")
    parser.add_argument("--inventory", default="cluster/lobsters.json", help="JSON inventory path")
    parser.add_argument("--scrape-configs", default="prometheus/scrape_configs/openclaw-gateways.yml", help="Prometheus scrape_config_files output path")
    parser.add_argument("--patches-dir", default="cluster/openclaw-monitoring-patches", help="Directory for per-gateway OpenClaw monitoring patch JSON")
    parser.add_argument("--otel-endpoint", help="Override OTLP HTTP base endpoint for generated OpenClaw patches")
    parser.add_argument("--include-disabled", action="store_true", help="Render disabled gateways too")
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    data = json.loads(inventory_path.read_text())
    defaults = data.get("defaults", {})
    items = [merged(defaults, gateway) for gateway in data.get("gateways", [])]
    gateway_ids = [item["gateway_id"] for item in items]
    if len(gateway_ids) != len(set(gateway_ids)):
        raise ValueError("inventory contains duplicate gateway_id values")
    if args.otel_endpoint:
        for item in items:
            item["otel_endpoint"] = args.otel_endpoint
    if not args.include_disabled:
        items = [item for item in items if is_enabled(item)]
    scrape_configs_path = Path(args.scrape_configs)
    scrape_configs_path.parent.mkdir(parents=True, exist_ok=True)
    scrape_configs_path.write_text(render_scrape_configs(items))

    patches_dir = Path(args.patches_dir)
    patches_dir.mkdir(parents=True, exist_ok=True)
    expected_patches = {f"{item['gateway_id']}.json" for item in items}
    for stale_patch in patches_dir.glob("*.json"):
        if stale_patch.name not in expected_patches:
            stale_patch.unlink()
    for item in items:
        (patches_dir / f"{item['gateway_id']}.json").write_text(render_otel_patch(item))

    print(f"generated {scrape_configs_path} with {len(items)} per-gateway scrape jobs")
    print(f"generated OpenClaw diagnostics patches in {patches_dir}")


if __name__ == "__main__":
    main()
