#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STACK="$ROOT/scripts/local_management_stack.sh"

cleanup() {
  "$STACK" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"$STACK" reset
"$STACK" start >"$ROOT/.local-data/management-stack/supervisor-test.log" 2>&1 &
SUPERVISOR_PID=$!

for _ in $(seq 1 150); do
  if [[ -f "$ROOT/.local-data/management-stack/api.port" && -f "$ROOT/.local-data/management-stack/mcp.port" && -f "$ROOT/.local-data/management-stack/frontend.port" ]]; then
    API_PORT=$(cat "$ROOT/.local-data/management-stack/api.port")
    MCP_PORT=$(cat "$ROOT/.local-data/management-stack/mcp.port")
    FRONTEND_PORT=$(cat "$ROOT/.local-data/management-stack/frontend.port")
    if curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1 \
      && curl -fsS "http://127.0.0.1:$MCP_PORT/healthz" >/dev/null 2>&1 \
      && curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null 2>&1; then
      break
    fi
  fi
  if ! kill -0 "$SUPERVISOR_PID" >/dev/null 2>&1; then
    sed -n '1,160p' "$ROOT/.local-data/management-stack/supervisor-test.log" >&2
    exit 1
  fi
  sleep 0.1
done

API_PORT=$(cat "$ROOT/.local-data/management-stack/api.port")
MCP_PORT=$(cat "$ROOT/.local-data/management-stack/mcp.port")
FRONTEND_PORT=$(cat "$ROOT/.local-data/management-stack/frontend.port")
DB_PORT=$(cat "$ROOT/.local-data/management-stack/database.port")
curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null
curl -fsS "http://127.0.0.1:$MCP_PORT/healthz" >/dev/null
curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null

mcp_unauthorized_status=$(curl -sS -o /dev/null -w '%{http_code}' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"local-stack-test","version":"1"}}}' \
  "http://127.0.0.1:$MCP_PORT/mcp")
[[ "$mcp_unauthorized_status" == "401" ]]

LOCAL_OPENCLAW_ADOPTED=false
if rg -q '"adopted": true' "$ROOT/.local-data/management-stack/local-openclaw-adoption.log" 2>/dev/null; then
  LOCAL_OPENCLAW_ADOPTED=true
fi

"$ROOT/.venv-api/bin/python" - "$API_PORT" "$LOCAL_OPENCLAW_ADOPTED" "$ROOT" "$DB_PORT" "$MCP_PORT" <<'PY'
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, sys.argv[3])
from scripts.authenticated_api_client import AuthenticatedApiClient

secret_root = Path(sys.argv[3]) / ".local-data" / "management-stack"
admin_password = (secret_root / "admin.password").read_text(encoding="utf-8").strip()
writer_password = (secret_root / "writer.password").read_text(encoding="utf-8").strip()
api = AuthenticatedApiClient(
    f"http://127.0.0.1:{sys.argv[1]}",
    "admin",
    admin_password,
    f"postgresql://agentswatch_app:{writer_password}@127.0.0.1:{sys.argv[4]}/agentswatch_local",
)

def get(path):
    status, response = api.request("GET", path)
    assert status == 200
    return response

def request(method, path, payload):
    return api.request(method, path, payload)

health = get("/healthz")
assert health["database_user"] == "agentswatch_reader"
assert health["transaction_read_only"] == "on"

overview = get("/api/v1/overview")["data"]["counts"]
assert overview["servers"] == 3
assert overview["runtimes"] == 3
assert overview["primary_agents"] == 4

catalog = get("/api/v1/automation/operations")["data"]["operations"]
server_tasks = [item for item in catalog if item["scope"] == "server"]
assert len(server_tasks) == 4
assert all(item["description"] and item["category"] and item["version"] for item in server_tasks)
server_task_map = {item["code"]: item for item in server_tasks}
assert server_task_map["server.bootstrap_ssh"]["risk_level"] == "medium"
assert server_task_map["server.bootstrap_ssh"]["supports_batch"] is False
assert all(
    server_task_map[code]["supports_batch"]
    for code in ("server.check_connection", "server.collect_facts", "server.discover_runtimes")
)

group = get("/api/v1/groups/00000000-0000-4000-8000-000000000801")["data"]
assert group["resource_type"] == "server"
assert len(group["members"]) == 1

issues = get("/api/v1/data-quality/issues?limit=100")
assert issues["page"]["total"] == 1
assert issues["data"][0]["raw_payload"]["name"] == ""

agents = get("/api/v1/agents?limit=100")
assert agents["page"]["total"] == 7
assert all(item["channel_type"] == "none" for item in agents["data"])
primary_agent = get("/api/v1/agents/00000000-0000-4000-8000-000000000901")["data"]
assert primary_agent["channel"] == {"channel_type": "none"}

if sys.argv[2] == "true":
    local_server = get("/api/v1/servers/00000000-0000-4000-8000-000000000501")["data"]
    local_runtime = get("/api/v1/runtimes/00000000-0000-4000-8000-000000000701")["data"]
    assert local_server["name"] == "本机开发服务器"
    assert local_server["connection"]["connection_type"] == "local"
    assert local_runtime["management_driver"] == "launchd"
    assert local_runtime["management_target"]
    assert local_runtime["web_endpoint"]["credential_configured"] is True

class RuntimeWebUiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"agentswatch runtime webui integration"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return

web_ui = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeWebUiHandler)
threading.Thread(target=web_ui.serve_forever, daemon=True).start()
web_ui_port = web_ui.server_address[1]

status, created = request("POST", "/api/v1/servers", {
    "environment_id": "00000000-0000-4000-8000-000000000101",
    "name": "本机联调服务器",
    "lifecycle_status": "active",
    "internal_ip": "127.0.0.2",
})
assert status == 201
server_id = created["data"]["server_id"]
status, connected = request("PUT", f"/api/v1/servers/{server_id}/connection", {
    "connection_type": "local",
    "host": "127.0.0.1",
})
assert status == 200
assert connected["data"]["connection"]["connection_type"] == "local"
status, started = request("POST", "/api/v1/automation/runs", {
    "operation_code": "server.collect_facts",
    "server_ids": [server_id],
})
assert status == 202
run_id = started["data"]["run_id"]
for _ in range(120):
    run = get(f"/api/v1/automation/runs/{run_id}")["data"]
    if run["status"] not in {"queued", "running"}:
        break
    time.sleep(0.1)
assert run["status"] == "succeeded"
detail = get(f"/api/v1/servers/{server_id}")["data"]
assert detail["connection"]["last_facts"]["hostname"]

status, started = request("POST", "/api/v1/automation/runs", {
    "operation_code": "server.discover_runtimes",
    "server_ids": [server_id],
    "parameters": {"runtime_search_paths": ["/tmp"]},
})
assert status == 202
run_id = started["data"]["run_id"]
for _ in range(120):
    run = get(f"/api/v1/automation/runs/{run_id}")["data"]
    if run["status"] not in {"queued", "running"}:
        break
    time.sleep(0.1)
assert run["status"] == "succeeded"
assert run["parameters"] == {"runtime_search_paths": ["/tmp"]}
assert run["hosts"][0]["result"]["paths"] == ["/tmp"]

status, runtime = request("POST", "/api/v1/runtimes", {
    "server_id": server_id,
    "runtime_family_id": "00000000-0000-4000-8000-000000000601",
    "name": "本机裸金属运行时",
    "deployment_mode": "baremetal",
    "lifecycle_status": "active",
    "endpoint_port": web_ui_port,
    "management_driver": "mock",
    "management_target": "openclaw-local-test",
})
assert status == 201
runtime_id = runtime["data"]["runtime_id"]
status, started = request("POST", "/api/v1/automation/runs", {
    "operation_code": "runtime.stop",
    "runtime_ids": [runtime_id],
})
assert status == 202
run_id = started["data"]["run_id"]
for _ in range(120):
    run = get(f"/api/v1/automation/runs/{run_id}")["data"]
    if run["status"] not in {"queued", "running"}:
        break
    time.sleep(0.1)
assert run["status"] == "succeeded"
runtime = get(f"/api/v1/runtimes/{runtime_id}")["data"]
assert runtime["observed_status"] == "stopped"

status, endpoint = request("PUT", f"/api/v1/runtimes/{runtime_id}/web-endpoint", {
    "display_name": "OpenClaw Control UI",
    "access_mode": "local_ssh_tunnel",
    "scheme": "http",
    "remote_host": "127.0.0.1",
    "base_path": "/",
    "auth_mode": "token",
    "token": "local-stack-token",
    "enabled": True,
})
assert status == 200
assert endpoint["data"]["credential_configured"] is True
assert "encrypted_credential" not in endpoint["data"]

status, access = request("POST", f"/api/v1/runtimes/{runtime_id}/web-access-sessions", {})
assert status == 201
launch = access["data"]
parsed = urlsplit(launch["launch_url"])
assert parsed.hostname == "127.0.0.1"
assert parsed.port == web_ui_port
assert parsed.fragment == "token=local-stack-token"
with urlopen(launch["launch_url"].split("#", 1)[0], timeout=3) as response:
    assert response.read() == b"agentswatch runtime webui integration"
status, closed = request("DELETE", f"/api/v1/runtime-web-access-sessions/{launch['access_session_id']}", None)
assert status == 200
assert closed["data"]["status"] == "closed"
web_ui.shutdown()
web_ui.server_close()

# Exercise the actual MCP process through a user-owned API Key. The MCP tool
# must reach this same REST API and return its standard response envelope.
_, key_status = api.request("GET", "/api/v1/auth/api-key")
key_path = "/api/v1/auth/api-key/rotate" if key_status["data"]["active"] else "/api/v1/auth/api-key"
_, key_response = api.request("POST", key_path, {})
api_key = key_response["data"]["api_key"]

async def verify_mcp():
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(
            f"http://127.0.0.1:{sys.argv[5]}/mcp",
            http_client=http_client,
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {"list_servers", "run_playbook", "get_task"}.issubset(
                    {tool.name for tool in tools.tools}
                )
                result = await session.call_tool("list_servers", {"limit": 1})
                structured = result.structuredContent
                assert structured["ok"] is True
                assert structured["result"]["page"]["total"] >= 1

asyncio.run(verify_mcp())
api.request("DELETE", "/api/v1/auth/api-key")
PY

if [[ "$LOCAL_OPENCLAW_ADOPTED" == "true" ]]; then
  ADMIN_PASSWORD=$(<"$ROOT/.local-data/management-stack/admin.password")
  WRITER_PASSWORD=$(<"$ROOT/.local-data/management-stack/writer.password")
  AGENTSWATCH_API_USERNAME=admin \
    AGENTSWATCH_API_PASSWORD="$ADMIN_PASSWORD" \
    AGENTSWATCH_WRITE_DATABASE_URL="postgresql://agentswatch_app:$WRITER_PASSWORD@127.0.0.1:$DB_PORT/agentswatch_local" \
    "$ROOT/.venv-api/bin/python" "$ROOT/scripts/verify_local_openclaw_management.py" \
    --api-url "http://127.0.0.1:$API_PORT"
fi

curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" | rg -q 'AgentsWatch'
(cd "$ROOT/frontend" && npm test && npm run build)
"$STACK" status

echo "local management stack integration passed"
