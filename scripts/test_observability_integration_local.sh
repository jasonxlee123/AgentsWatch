#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STACK="$ROOT/scripts/local_management_stack.sh"
LOCAL_DIR="$ROOT/.local-data/management-stack"
CONTROL_STATE_ROOT="$ROOT/.local-data/observability-control"
SUPERVISOR_LOG="$LOCAL_DIR/observability-integration-test.log"

cleanup() {
  "$STACK" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

PROMETHEUS_RELOAD_URL=""
if curl -fsS "http://127.0.0.1:9090/-/ready" >/dev/null 2>&1; then
  PROMETHEUS_RELOAD_URL="http://127.0.0.1:9090/-/reload"
fi
AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT="$CONTROL_STATE_ROOT" \
AGENTSWATCH_PROMETHEUS_RELOAD_URL="$PROMETHEUS_RELOAD_URL" \
  "$STACK" start >"$SUPERVISOR_LOG" 2>&1 &
SUPERVISOR_PID=$!

for _ in $(seq 1 240); do
  if [[ -f "$LOCAL_DIR/api.port" && -f "$LOCAL_DIR/observability-control.port" ]]; then
    API_PORT=$(cat "$LOCAL_DIR/api.port")
    CONTROL_PORT=$(cat "$LOCAL_DIR/observability-control.port")
    if curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1 \
      && curl -fsS "http://127.0.0.1:$CONTROL_PORT/healthz" >/dev/null 2>&1; then
      break
    fi
  fi
  if ! kill -0 "$SUPERVISOR_PID" >/dev/null 2>&1; then
    sed -n '1,180p' "$SUPERVISOR_LOG" >&2
    exit 1
  fi
  sleep 0.25
done

API_PORT=$(cat "$LOCAL_DIR/api.port")
CONTROL_PORT=$(cat "$LOCAL_DIR/observability-control.port")
DB_PORT=$(cat "$LOCAL_DIR/database.port")
CONTROL_TOKEN=$(cat "$LOCAL_DIR/observability-control.token")
ADMIN_PASSWORD=$(cat "$LOCAL_DIR/admin.password")
WRITER_PASSWORD=$(cat "$LOCAL_DIR/writer.password")

AGENTSWATCH_API_USERNAME="${AGENTSWATCH_LOCAL_ADMIN_USERNAME:-admin}" \
AGENTSWATCH_API_PASSWORD="${AGENTSWATCH_LOCAL_ADMIN_PASSWORD:-$ADMIN_PASSWORD}" \
AGENTSWATCH_WRITE_DATABASE_URL="postgresql://agentswatch_app:$WRITER_PASSWORD@127.0.0.1:$DB_PORT/agentswatch_local" \
AGENTSWATCH_CONTROL_TOKEN="$CONTROL_TOKEN" \
"$ROOT/.venv-api/bin/python" - "$API_PORT" "$CONTROL_PORT" "$ROOT" <<'PY'
import json
import os
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, sys.argv[3])
from scripts.authenticated_api_client import AuthenticatedApiClient

runtime_id = "00000000-0000-4000-8000-000000000701"
api = AuthenticatedApiClient(
    f"http://127.0.0.1:{sys.argv[1]}",
    os.environ["AGENTSWATCH_API_USERNAME"],
    os.environ["AGENTSWATCH_API_PASSWORD"],
    os.environ["AGENTSWATCH_WRITE_DATABASE_URL"],
)

def call(method, path, body=None, expected=200):
    status, response = api.request(method, path, body)
    if status != expected:
        raise RuntimeError(f"{method} {path} returned {status}: {response}")
    return response["data"]

runtime = call("GET", f"/api/v1/runtimes/{runtime_id}")
if runtime["management_driver"] != "launchd" or runtime["endpoint_port"] != 16402:
    raise RuntimeError("this machine's bare-metal OpenClaw was not adopted as the local Runtime")
if len(runtime.get("active_deployments") or []) != 1:
    raise RuntimeError("the local Runtime must expose exactly one active primary agent")

status = call("GET", "/api/v1/observability-integration/status")
if status.get("adapter_status") not in {"connected", "ready"}:
    raise RuntimeError(f"management proxy did not reach adapter: {status}")

preview = call(
    "POST",
    "/api/v1/observability-integration/sync/preview",
    {"source": "management-db"},
)
snapshot_hash = preview.get("snapshot_hash", "")
if len(snapshot_hash) != 64:
    raise RuntimeError("preview did not return a canonical snapshot hash")
changes = preview.get("changes") or preview.get("actions") or []
local = next((item for item in changes if item.get("runtime_id") == runtime_id), None)
if local is None or not local.get("primary_agent_id") or not local.get("display_name"):
    raise RuntimeError("local Runtime preview lost its primary-agent display identity")

job = call(
    "POST",
    "/api/v1/observability-integration/sync/apply",
    {
        "source": "management-db",
        "expected_snapshot_hash": snapshot_hash,
        "confirmed": True,
    },
    expected=202,
)
if job.get("status") != "succeeded":
    raise RuntimeError(f"adapter sync did not succeed: {job}")
result = job.get("result") or {}
revision_id = result.get("revision_id") or job.get("revision_id")
if not revision_id:
    raise RuntimeError("sync did not activate a revision")

request = Request(
    f"http://127.0.0.1:{sys.argv[2]}/metrics",
    headers={"Authorization": f"Bearer {os.environ['AGENTSWATCH_CONTROL_TOKEN']}"},
)
with urlopen(request, timeout=5) as response:
    metrics = response.read().decode("utf-8")
if "agentswatch_gateway_info" not in metrics or f'runtime_id="{runtime_id}"' not in metrics:
    raise RuntimeError("adapter metrics do not expose the local Runtime identity")
if f'primary_agent_id="{local["primary_agent_id"]}"' not in metrics:
    raise RuntimeError("adapter metrics do not expose the local primary Agent identity")

print(json.dumps({
    "verified": True,
    "runtime_id": runtime_id,
    "gateway_id": local.get("gateway_id"),
    "primary_agent_id": local["primary_agent_id"],
    "display_name": local["display_name"],
    "revision_id": revision_id,
    "snapshot_hash": snapshot_hash,
}, ensure_ascii=False))
PY

if [[ -n "$PROMETHEUS_RELOAD_URL" ]]; then
  identity_query='agentswatch_gateway_info{runtime_id="00000000-0000-4000-8000-000000000701",primary_agent_id="00000000-0000-4000-8000-000000000901"}'
  identity_ready=false
  for _ in $(seq 1 30); do
    series_count=$(
      curl -fsS --get "http://127.0.0.1:9090/api/v1/query" \
        --data-urlencode "query=$identity_query" | jq -r '.data.result | length'
    )
    if [[ "$series_count" -gt 0 ]]; then
      identity_ready=true
      break
    fi
    sleep 0.5
  done
  if [[ "$identity_ready" != true ]]; then
    echo "Prometheus did not load the active agentswatch_gateway_info revision" >&2
    exit 1
  fi
fi

CURRENT="$CONTROL_STATE_ROOT/current"
GATEWAY_ID=$(
  "$ROOT/.venv-api/bin/python" - "$CURRENT/snapshot.json" <<'PY'
import json
import sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))["rows"]
runtime_id = "00000000-0000-4000-8000-000000000701"
print(next(row["gateway_id"] for row in rows if row["runtime_id"] == runtime_id))
PY
)
"$ROOT/scripts/apply_lobster_config.sh" "$GATEWAY_ID" \
  --patch-dir "$CURRENT/openclaw-monitoring-patches" --dry-run
openclaw gateway status --deep | rg -q 'Connectivity probe: ok'
