#!/usr/bin/env python3
"""Adopt this development Mac and its bare-metal OpenClaw into the local stack."""

import argparse
import getpass
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
from pathlib import Path

from authenticated_api_client import AuthenticatedApiClient


DEFAULT_SERVER_ID = "00000000-0000-4000-8000-000000000501"
DEFAULT_RUNTIME_ID = "00000000-0000-4000-8000-000000000701"


def command(*argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)


def memory_gib():
    if platform.system() == "Darwin":
        result = command("/usr/sbin/sysctl", "-n", "hw.memsize")
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return round(int(result.stdout.strip()) / 1073741824, 2)
    return None


def openclaw_details():
    executable = shutil.which("openclaw")
    if not executable:
        return None, "openclaw command is not installed"
    status = command(executable, "gateway", "status", "--deep")
    if status.returncode != 0 or "Runtime: running" not in status.stdout:
        return None, "OpenClaw Gateway is not running"
    version_result = command(executable, "--version")
    version_match = re.search(r"OpenClaw\s+([^\s]+)", version_result.stdout)
    port_match = re.search(r"Listening:\s+127\.0\.0\.1:(\d+)", status.stdout)
    if not port_match:
        port_match = re.search(r"Gateway:.*?port=(\d+)", status.stdout)
    if not port_match:
        return None, "OpenClaw Gateway port could not be detected"

    service_file_match = re.search(r"^Service file:\s+(.+)$", status.stdout, re.MULTILINE)
    service_label = None
    if service_file_match:
        service_file = Path(service_file_match.group(1).strip()).expanduser()
        try:
            with service_file.open("rb") as handle:
                service_label = plistlib.load(handle).get("Label")
        except (OSError, plistlib.InvalidFileException):
            service_label = None
    if "Service: LaunchAgent" not in status.stdout or not service_label:
        return None, "only a running macOS LaunchAgent is adopted automatically"

    config_path = Path.home() / ".openclaw" / "openclaw.json"
    auth_mode = None
    credential = None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        auth = ((config.get("gateway") or {}).get("auth") or {})
        if auth.get("mode") in {"token", "password"} and isinstance(auth.get(auth["mode"]), str):
            auth_mode = auth["mode"]
            credential = auth[auth_mode]
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        pass

    return {
        "version": version_match.group(1) if version_match else None,
        "port": int(port_match.group(1)),
        "service_label": service_label,
        "config_path": str(config_path),
        "state_path": str(config_path.parent),
        "auth_mode": auth_mode,
        "credential": credential,
    }, None


def adopt(api, server_id, runtime_id):
    details, reason = openclaw_details()
    if details is None:
        return {"adopted": False, "reason": reason}

    _, server_response = api.request("GET", f"/api/v1/servers/{server_id}")
    server = server_response["data"]
    disk = shutil.disk_usage("/")
    hostname = socket.gethostname()
    server_payload = {
        "row_version": server["row_version"],
        "name": "本机开发服务器",
        "provider_instance_id": f"local:{hostname}",
        "cloud_provider_id": None,
        "lifecycle_status": "active",
        "cpu_cores": os.cpu_count(),
        "memory_gib": memory_gib(),
        "disk_gib": round(disk.total / 1073741824, 2),
        "internal_ip": "127.0.0.1",
        "public_ip": None,
        "os_name": platform.system() or "macOS",
        "os_version": platform.mac_ver()[0] or platform.release(),
        "architecture": platform.machine(),
        "work_account": getpass.getuser(),
    }
    server_payload = {key: value for key, value in server_payload.items() if value is not None or key in {"cloud_provider_id", "public_ip"}}
    api.request("PATCH", f"/api/v1/servers/{server_id}", server_payload)

    connection = server.get("connection")
    connection_payload = {
        "connection_type": "local",
        "host": "127.0.0.1",
        "use_become": False,
    }
    if connection:
        connection_payload["row_version"] = connection["row_version"]
    api.request("PUT", f"/api/v1/servers/{server_id}/connection", connection_payload)

    _, runtime_response = api.request("GET", f"/api/v1/runtimes/{runtime_id}")
    runtime = runtime_response["data"]
    api.request("PATCH", f"/api/v1/runtimes/{runtime_id}", {
        "row_version": runtime["row_version"],
        "name": "本机 OpenClaw Runtime",
        "lifecycle_status": "active",
        "bind_address": "127.0.0.1",
        "endpoint_port": details["port"],
        "runtime_account": getpass.getuser(),
        "config_path": details["config_path"],
        "state_path": details["state_path"],
        "management_driver": "launchd",
        "management_target": details["service_label"],
    })

    endpoint_saved = False
    if details["auth_mode"] and details["credential"]:
        try:
            _, endpoint_response = api.request("GET", f"/api/v1/runtimes/{runtime_id}/web-endpoint")
            endpoint = endpoint_response["data"]
        except RuntimeError as exc:
            if "not found" not in str(exc).lower():
                raise
            endpoint = None
        endpoint_payload = {
            "display_name": "OpenClaw Control UI（本机）",
            "access_mode": "local_ssh_tunnel",
            "scheme": "http",
            "remote_host": "127.0.0.1",
            "port_override": details["port"],
            "base_path": "/",
            "auth_mode": details["auth_mode"],
            details["auth_mode"]: details["credential"],
            "enabled": True,
        }
        if endpoint:
            endpoint_payload["row_version"] = endpoint["row_version"]
        api.request("PUT", f"/api/v1/runtimes/{runtime_id}/web-endpoint", endpoint_payload)
        endpoint_saved = True

    return {
        "adopted": True,
        "server_id": server_id,
        "runtime_id": runtime_id,
        "runtime_version": details["version"],
        "runtime_port": details["port"],
        "management_driver": "launchd",
        "management_target": details["service_label"],
        "web_credential_saved": endpoint_saved,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--server-id", default=DEFAULT_SERVER_ID)
    parser.add_argument("--runtime-id", default=DEFAULT_RUNTIME_ID)
    args = parser.parse_args()
    username = os.environ.get("AGENTSWATCH_API_USERNAME")
    password = os.environ.get("AGENTSWATCH_API_PASSWORD")
    database_url = os.environ.get("AGENTSWATCH_WRITE_DATABASE_URL")
    if not all((username, password, database_url)):
        parser.error(
            "AGENTSWATCH_API_USERNAME, AGENTSWATCH_API_PASSWORD and "
            "AGENTSWATCH_WRITE_DATABASE_URL are required"
        )
    api = AuthenticatedApiClient(args.api_url, username, password, database_url)
    print(json.dumps(adopt(api, args.server_id, args.runtime_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
