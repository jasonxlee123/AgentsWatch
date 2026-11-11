#!/usr/bin/env python3
"""Verify the complete runtime-operation and WebUI flow against local OpenClaw."""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from authenticated_api_client import AuthenticatedApiClient


DEFAULT_RUNTIME_ID = "00000000-0000-4000-8000-000000000701"


class LocalOpenClawVerifier:
    def __init__(self, api, runtime_id):
        self.api = api
        self.runtime_id = runtime_id

    def request(self, method, path, payload=None):
        _, response = self.api.request(method, path, payload)
        return response["data"]

    def run_operation(self, operation_code, timeout=70):
        run = self.request("POST", "/api/v1/automation/runs", {
            "operation_code": operation_code,
            "runtime_ids": [self.runtime_id],
            "requested_by": "local-openclaw-verification",
        })
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.request("GET", f"/api/v1/automation/runs/{run['run_id']}")
            if run["status"] not in {"queued", "running"}:
                return run
            time.sleep(0.1)
        raise RuntimeError(f"operation did not finish before timeout: {operation_code}")

    @staticmethod
    def endpoint_listening(port):
        with socket.socket() as probe:
            probe.settimeout(0.3)
            return probe.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def gateway_pid():
        executable = shutil.which("openclaw")
        if not executable:
            raise RuntimeError("openclaw command is not installed")
        status = subprocess.run(
            [executable, "gateway", "status", "--deep"],
            capture_output=True, text=True, timeout=20, check=True,
        )
        match = re.search(r"Runtime: running \(pid (\d+)\)", status.stdout)
        if not match:
            raise RuntimeError("OpenClaw Gateway PID was not reported")
        return int(match.group(1))

    @staticmethod
    def assert_operation(run, expected_status, endpoint_reachable):
        if run["status"] != "succeeded" or len(run["hosts"]) != 1:
            raise RuntimeError(f"runtime operation failed: {run['operation_code']}")
        result = run["hosts"][0].get("result") or {}
        if result.get("observed_status") != expected_status:
            raise RuntimeError(f"unexpected observed status after {run['operation_code']}")
        if result.get("endpoint_reachable") is not endpoint_reachable:
            raise RuntimeError(f"unexpected endpoint readiness after {run['operation_code']}")
        return result

    def restore_launchd(self, management_target, port):
        service = f"gui/{os.getuid()}/{management_target}"
        subprocess.run(
            ["/bin/launchctl", "enable", service],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not self.endpoint_listening(port):
            subprocess.run(
                ["/bin/launchctl", "kickstart", service],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(150):
                if self.endpoint_listening(port):
                    return
                time.sleep(0.1)

    def verify_webui(self, port):
        access = self.request(
            "POST", f"/api/v1/runtimes/{self.runtime_id}/web-access-sessions", {},
        )
        try:
            launch_url = access["launch_url"]
            with urlopen(launch_url.split("#", 1)[0], timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError("OpenClaw Control UI did not return HTTP 200")
            token = parse_qs(urlsplit(launch_url).fragment).get("token", [None])[0]
            if token:
                verified = subprocess.run(
                    [
                        shutil.which("openclaw") or "openclaw", "gateway", "call", "health",
                        "--url", f"ws://127.0.0.1:{port}", "--token", token, "--json",
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                )
                if verified.returncode != 0:
                    raise RuntimeError("OpenClaw authenticated WebSocket health call failed")
            elif not access.get("manual_credential"):
                raise RuntimeError("configured OpenClaw WebUI credential was not delivered")
        finally:
            self.request(
                "DELETE", f"/api/v1/runtime-web-access-sessions/{access['access_session_id']}",
            )

    def verify(self):
        runtime = self.request("GET", f"/api/v1/runtimes/{self.runtime_id}")
        if runtime.get("management_driver") != "launchd":
            raise RuntimeError("local runtime is not configured with launchd")
        port = runtime.get("endpoint_port")
        management_target = runtime.get("management_target")
        if not port or not management_target:
            raise RuntimeError("local runtime endpoint or launchd target is missing")

        pid_before = self.gateway_pid()
        try:
            collected = self.run_operation("runtime.collect_info")
            if collected["status"] != "succeeded":
                raise RuntimeError("runtime information collection failed")
            collection = collected["hosts"][0].get("result") or {}
            if not collection.get("actual_version") or collection.get("observed_agent_count") is not None:
                raise RuntimeError("runtime information collection returned an invalid observation")
            collected_again = self.run_operation("runtime.collect_info")
            duplicate = collected_again["hosts"][0].get("result") or {}
            if duplicate.get("snapshot_created") is not False:
                raise RuntimeError("unchanged runtime information created a duplicate snapshot")

            refreshed = self.run_operation("runtime.refresh_status")
            self.assert_operation(refreshed, "running", True)

            restarted = self.run_operation("runtime.restart")
            self.assert_operation(restarted, "running", True)
            if self.gateway_pid() == pid_before:
                raise RuntimeError("restart did not recreate the OpenClaw process")

            stopped = self.run_operation("runtime.stop")
            self.assert_operation(stopped, "stopped", False)
            if self.endpoint_listening(port):
                raise RuntimeError("OpenClaw endpoint is still listening after stop")

            started = self.run_operation("runtime.start")
            self.assert_operation(started, "running", True)
            if not self.endpoint_listening(port):
                raise RuntimeError("OpenClaw endpoint did not return after start")

            self.verify_webui(port)
        finally:
            self.restore_launchd(management_target, port)

        return {
            "verified": True,
            "runtime_id": self.runtime_id,
            "collect_info": "succeeded",
            "actual_version": collection["actual_version"],
            "snapshot_deduplicated": True,
            "refresh": "succeeded",
            "restart": "succeeded",
            "stop": "succeeded",
            "start": "succeeded",
            "endpoint_readiness": True,
            "webui_http": 200,
            "webui_authentication": "succeeded",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
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
    result = LocalOpenClawVerifier(api, args.runtime_id).verify()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
