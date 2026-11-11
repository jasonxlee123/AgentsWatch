#!/usr/bin/env python3
"""Fail closed on an expanded AgentsWatch production Compose model.

The validator reads ``docker compose config --format json`` from stdin.  It
never prints environment values because that model contains production
credentials.  Application and PostgreSQL projects are intentionally validated
separately; the former must use the external database network and the latter
must own an internal database network without publishing PostgreSQL.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit


DATABASE_NETWORK = "agentswatch-database"
APPLICATION_DATABASE_MEMBERS = {"management-api", "observability-control"}
PLACEHOLDER_IMAGES = ("registry.example.invalid", "example.invalid", "VERSION")
WEAK_SECRETS = {"admin", "change-me", "changeme", "minioadmin", "password"}
REQUIRED_APPLICATION_IMAGES = {
    "storage-init",
    "prometheus-secrets-init",
    "nginx-tls-init",
    "minio",
    "minio-init",
    "otel-collector",
    "clickhouse",
    "prometheus",
    "alertmanager",
    "thanos-sidecar",
    "thanos-store",
    "thanos-compact",
    "thanos-query",
    "loki",
    "tempo",
    "grafana",
    "management-api",
    "management-mcp",
    "observability-control",
    "combined-nginx",
}
APPLICATION_DATA_MOUNTS = {
    "alertmanager": ("/alertmanager", "/data/agentswatch/alertmanager"),
    "clickhouse": ("/var/lib/clickhouse", "/data/agentswatch/clickhouse"),
    "grafana": ("/var/lib/grafana", "/data/agentswatch/grafana"),
    "loki": ("/loki", "/data/agentswatch/loki"),
    "management-api": ("/data/agentswatch/management", "/data/agentswatch/management"),
    "minio": ("/data", "/data/agentswatch/minio"),
    "observability-control": (
        "/data/agentswatch/observability-control",
        "/data/agentswatch/observability-control",
    ),
    "otel-collector": ("/var/lib/otelcol", "/data/agentswatch/otel-collector"),
    "prometheus": ("/prometheus", "/data/agentswatch/prometheus"),
    "tempo": ("/var/tempo", "/data/agentswatch/tempo"),
    "thanos-compact": ("/var/thanos/compact", "/data/agentswatch/thanos-compact"),
    "thanos-store": ("/var/thanos/store", "/data/agentswatch/thanos-store"),
}


class ValidationError(RuntimeError):
    """A production invariant was not satisfied."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be a JSON object")
    return value


def _service_environment(service: Mapping[str, Any]) -> Mapping[str, Any]:
    value = service.get("environment", {})
    return _mapping(value, "service environment")


def _service_networks(service: Mapping[str, Any]) -> set[str]:
    value = service.get("networks", {})
    if isinstance(value, Mapping):
        return {str(item) for item in value}
    if isinstance(value, list):
        return {str(item) for item in value}
    raise ValidationError("service networks must be an object or array")


def _active(service: Mapping[str, Any]) -> bool:
    return not service.get("profiles")


def _pinned_image(image: str) -> bool:
    value = image.strip()
    if not value or any(marker.lower() in value.lower() for marker in PLACEHOLDER_IMAGES):
        return False
    if value.startswith("sha256:") or "@sha256:" in value:
        return True
    final_component = value.rsplit("/", 1)[-1]
    if ":" not in final_component:
        return False
    tag = final_component.rsplit(":", 1)[1].strip().lower()
    return bool(tag and tag != "latest")


def _assert_images(services: Mapping[str, Any]) -> list[str]:
    if "postgres" in services and "management-api" not in services:
        required = {"postgres"}
    else:
        required = REQUIRED_APPLICATION_IMAGES
    missing = sorted(required - set(services))
    if missing:
        raise ValidationError("required production services are missing: " + ", ".join(missing))
    images: list[str] = []
    inspected = set(required)
    if "legacy-openclaw-relay" in services:
        inspected.add("legacy-openclaw-relay")
    for name in sorted(inspected):
        service = _mapping(services[name], f"service {name}")
        image = str(service.get("image", ""))
        if not _pinned_image(image):
            raise ValidationError(f"service {name} does not use a pinned, non-placeholder image")
        images.append(image)
    return sorted(set(images))


def _assert_strong_secret(service: Mapping[str, Any], key: str, label: str) -> None:
    value = str(_service_environment(service).get(key, "")).strip()
    if not value or value.lower() in WEAK_SECRETS:
        raise ValidationError(f"{label} is empty or uses a known weak placeholder")


def _assert_required_value(service: Mapping[str, Any], key: str, label: str) -> None:
    if not str(_service_environment(service).get(key, "")).strip():
        raise ValidationError(f"{label} is empty")


def _assert_credentials(services: Mapping[str, Any]) -> None:
    if "postgres" in services and "management-api" not in services:
        return
    _assert_required_value(_mapping(services["minio"], "service minio"), "MINIO_ROOT_USER", "MinIO root user")
    _assert_strong_secret(_mapping(services["minio"], "service minio"), "MINIO_ROOT_PASSWORD", "MinIO root password")
    _assert_required_value(_mapping(services["clickhouse"], "service clickhouse"), "CLICKHOUSE_USER", "ClickHouse user")
    _assert_strong_secret(_mapping(services["clickhouse"], "service clickhouse"), "CLICKHOUSE_PASSWORD", "ClickHouse password")
    _assert_required_value(_mapping(services["grafana"], "service grafana"), "GF_SECURITY_ADMIN_USER", "Grafana admin user")
    _assert_strong_secret(_mapping(services["grafana"], "service grafana"), "GF_SECURITY_ADMIN_PASSWORD", "Grafana admin password")


def _assert_database_url(value: Any, label: str, expected_host: str | None) -> None:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid URL") from exc
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValidationError(f"{label} must use a PostgreSQL URL")
    if not parsed.hostname:
        raise ValidationError(f"{label} must include a database host")
    if expected_host is not None and parsed.hostname != expected_host:
        raise ValidationError(f"{label} must target {expected_host}")
    if expected_host is None and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValidationError(f"{label} cannot target container-local loopback")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("sslmode") != ["verify-full"]:
        raise ValidationError(f"{label} must set sslmode=verify-full")


def _assert_application_database(
    model: Mapping[str, Any], services: Mapping[str, Any]
) -> None:
    if "management-api" not in services:
        return
    networks = _mapping(model.get("networks", {}), "networks")
    expected_host = "agentswatch-db" if DATABASE_NETWORK in networks else None
    management = _service_environment(_mapping(services["management-api"], "service management-api"))
    adapter = _service_environment(_mapping(services["observability-control"], "service observability-control"))
    for key in ("AGENTSWATCH_DATABASE_URL", "AGENTSWATCH_WRITE_DATABASE_URL"):
        _assert_database_url(management.get(key), key, expected_host)
    _assert_database_url(
        adapter.get("AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL"),
        "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL",
        expected_host,
    )
    for key in (
        "AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY",
        "AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY",
    ):
        if str(adapter.get(key, "")).strip().lower() != "false":
            raise ValidationError(f"{key} must remain false in the production model")


def _port_fields(port: Any) -> tuple[str, int, int]:
    value = _mapping(port, "published port")
    host_ip = str(value.get("host_ip") or "0.0.0.0")
    try:
        published = int(value.get("published"))
        target = int(value.get("target"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("published ports must use numeric published and target values") from exc
    return host_ip, published, target


def _assert_ports(services: Mapping[str, Any]) -> None:
    postgres = services.get("postgres")
    if isinstance(postgres, Mapping) and postgres.get("ports"):
        raise ValidationError("PostgreSQL must not publish a host port")
    if "combined-nginx" not in services:
        return
    public: list[tuple[str, str, int, int]] = []
    for name, raw_service in services.items():
        service = _mapping(raw_service, f"service {name}")
        if not _active(service):
            continue
        for raw_port in service.get("ports", []) or []:
            host_ip, published, target = _port_fields(raw_port)
            if host_ip in {"0.0.0.0", "::", ""}:
                public.append((str(name), host_ip, published, target))
    allowed = {
        ("combined-nginx", "0.0.0.0", 443, 443),
        ("otel-collector", "0.0.0.0", 4318, 4318),
    }
    if set(public) != allowed:
        raise ValidationError("public listeners must be exactly combined-nginx:443 and otel-collector:4318")


def _bind_mounts(service: Mapping[str, Any]) -> dict[str, str]:
    mounts: dict[str, str] = {}
    for raw_volume in service.get("volumes", []) or []:
        volume = _mapping(raw_volume, "service volume")
        if volume.get("type") == "bind":
            mounts[str(volume.get("target", ""))] = str(volume.get("source", ""))
    return mounts


def _assert_data_mounts(services: Mapping[str, Any]) -> None:
    if "postgres" in services and "management-api" not in services:
        expected = {
            "/var/lib/postgresql/data": "/data/agentswatch/management/postgres"
        }
        actual = _bind_mounts(_mapping(services["postgres"], "service postgres"))
        for target, source in expected.items():
            if actual.get(target) != source:
                raise ValidationError("PostgreSQL data mount must use /data/agentswatch/management/postgres")
        return
    for name, (target, source) in APPLICATION_DATA_MOUNTS.items():
        actual = _bind_mounts(_mapping(services[name], f"service {name}"))
        if actual.get(target) != source:
            raise ValidationError(f"service {name} data mount does not use the production /data path")


def _assert_ingress_profiles(services: Mapping[str, Any]) -> None:
    for name in ("nginx", "management-nginx"):
        if name not in services:
            # Compose omits inactive profiled services from ``config`` when
            # COMPOSE_PROFILES is explicitly empty.
            continue
        service = _mapping(services.get(name), f"service {name}")
        profiles = service.get("profiles")
        if profiles != ["standalone-ingress"]:
            raise ValidationError(f"{name} must remain disabled behind standalone-ingress")
    combined = _mapping(services.get("combined-nginx"), "service combined-nginx")
    if combined.get("profiles"):
        raise ValidationError("combined-nginx must be active without a profile")


def _assert_database_network(model: Mapping[str, Any], services: Mapping[str, Any]) -> None:
    networks = _mapping(model.get("networks", {}), "networks")
    members = {
        str(name)
        for name, raw_service in services.items()
        if DATABASE_NETWORK in _service_networks(_mapping(raw_service, f"service {name}"))
    }
    if DATABASE_NETWORK not in networks:
        if members:
            raise ValidationError("services reference an undeclared agentswatch-database network")
        if "postgres" in services:
            raise ValidationError("database project must declare agentswatch-database")
        return
    network = _mapping(networks.get(DATABASE_NETWORK), f"network {DATABASE_NETWORK}")
    if "postgres" in services and "management-api" not in services:
        if members != {"postgres"}:
            raise ValidationError("database project network member must be exactly postgres")
        if network.get("internal") is not True:
            raise ValidationError("database project network must be internal")
    else:
        if members != APPLICATION_DATABASE_MEMBERS:
            raise ValidationError("application database network members must be exactly management-api and observability-control")
        if network.get("external") is not True:
            raise ValidationError("application database network must be external")


def validate(model: Mapping[str, Any]) -> list[str]:
    services = _mapping(model.get("services", {}), "services")
    images = _assert_images(services)
    _assert_credentials(services)
    _assert_application_database(model, services)
    if "management-api" in services:
        _assert_ingress_profiles(services)
    _assert_ports(services)
    _assert_data_mounts(services)
    _assert_database_network(model, services)
    return images


def _inspect_images(images: list[str], output: Path) -> None:
    records: list[dict[str, Any]] = []
    for image in images:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        if not isinstance(data, list) or len(data) != 1:
            raise ValidationError(f"docker returned an unexpected inspection result for {image}")
        item = _mapping(data[0], f"image inspection for {image}")
        if item.get("Os") != "linux" or item.get("Architecture") != "amd64":
            raise ValidationError(f"image {image} is not loaded as linux/amd64")
        records.append(
            {
                "architecture": item.get("Architecture"),
                "id": item.get("Id"),
                "image": image,
                "os": item.get("Os"),
                "repo_digests": item.get("RepoDigests") or [],
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"images": records}, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="validate an expanded production Compose JSON model without printing secrets"
    )
    parser.add_argument(
        "--image-record",
        type=Path,
        help="create a mode-0600 JSON record of the locally loaded image IDs",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        model = _mapping(json.load(sys.stdin), "Compose model")
        images = validate(model)
        if args.image_record is not None:
            _inspect_images(images, args.image_record)
    except (ValidationError, json.JSONDecodeError, OSError, subprocess.SubprocessError) as exc:
        print(f"production release validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"production release validation passed ({len(images)} pinned images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
