#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ACTION=${1:-start}
LOCAL_DIR="$ROOT/.local-data/management-stack"
COMPOSE_FILE="$ROOT/docker-compose.local.yml"
API_PID_FILE="$LOCAL_DIR/api.pid"
MCP_PID_FILE="$LOCAL_DIR/mcp.pid"
FRONTEND_PID_FILE="$LOCAL_DIR/frontend.pid"
OBSERVABILITY_PID_FILE="$LOCAL_DIR/observability-control.pid"
SUPERVISOR_PID_FILE="$LOCAL_DIR/supervisor.pid"
DB_PORT_FILE="$LOCAL_DIR/database.port"
API_PORT_FILE="$LOCAL_DIR/api.port"
MCP_PORT_FILE="$LOCAL_DIR/mcp.port"
FRONTEND_PORT_FILE="$LOCAL_DIR/frontend.port"
OBSERVABILITY_PORT_FILE="$LOCAL_DIR/observability-control.port"
API_LOG="$LOCAL_DIR/api.log"
MCP_LOG="$LOCAL_DIR/mcp.log"
FRONTEND_LOG="$LOCAL_DIR/frontend.log"
OBSERVABILITY_LOG="$LOCAL_DIR/observability-control.log"
LOCAL_ADOPTION_LOG="$LOCAL_DIR/local-openclaw-adoption.log"
WEB_CREDENTIAL_KEY_FILE="$LOCAL_DIR/web-credential.key"
SSH_CREDENTIAL_KEY_FILE="$LOCAL_DIR/ssh-credential.key"
MANAGED_SSH_PRIVATE_KEY_FILE="$LOCAL_DIR/agentswatch-managed"
OBSERVABILITY_TOKEN_FILE="$LOCAL_DIR/observability-control.token"
POSTGRES_PASSWORD_FILE="$LOCAL_DIR/postgres.password"
READER_PASSWORD_FILE="$LOCAL_DIR/reader.password"
WRITER_PASSWORD_FILE="$LOCAL_DIR/writer.password"
OBSERVABILITY_DATABASE_PASSWORD_FILE="$LOCAL_DIR/observability-database.password"
ADMIN_PASSWORD_FILE="$LOCAL_DIR/admin.password"
OBSERVABILITY_STATE_ROOT="${AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT:-$LOCAL_DIR/observability-control}"

mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_DIR"

ensure_secret_file() {
  local file=$1
  if [[ ! -s "$file" ]]; then
    python3 - <<'PY' > "$file"
import secrets
print(secrets.token_urlsafe(32))
PY
  fi
  chmod 600 "$file"
}

ensure_local_secrets() {
  ensure_secret_file "$POSTGRES_PASSWORD_FILE"
  ensure_secret_file "$READER_PASSWORD_FILE"
  ensure_secret_file "$WRITER_PASSWORD_FILE"
  ensure_secret_file "$OBSERVABILITY_DATABASE_PASSWORD_FILE"
  ensure_secret_file "$ADMIN_PASSWORD_FILE"
}

ensure_local_secrets

POSTGRES_PASSWORD="${AGENTSWATCH_LOCAL_POSTGRES_PASSWORD:-$(<"$POSTGRES_PASSWORD_FILE")}"
READER_PASSWORD="${AGENTSWATCH_LOCAL_READER_PASSWORD:-$(<"$READER_PASSWORD_FILE")}"
WRITER_PASSWORD="${AGENTSWATCH_LOCAL_WRITER_PASSWORD:-$(<"$WRITER_PASSWORD_FILE")}"
OBSERVABILITY_DATABASE_PASSWORD="${AGENTSWATCH_LOCAL_OBSERVABILITY_DATABASE_PASSWORD:-$(<"$OBSERVABILITY_DATABASE_PASSWORD_FILE")}"
ADMIN_PASSWORD="${AGENTSWATCH_LOCAL_ADMIN_PASSWORD:-$(<"$ADMIN_PASSWORD_FILE")}"

free_port() {
  python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

choose_port() {
  local explicit=$1
  local file=$2
  local preferred=$3
  if [[ -n "$explicit" ]]; then
    echo "$explicit"
  elif [[ -f "$file" ]]; then
    cat "$file"
  elif ! lsof -nP -iTCP:"$preferred" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "$preferred"
  else
    free_port
  fi
}

API_PORT=$(choose_port "${AGENTSWATCH_LOCAL_API_PORT:-}" "$API_PORT_FILE" 8000)
MCP_PORT=$(choose_port "${AGENTSWATCH_LOCAL_MCP_PORT:-}" "$MCP_PORT_FILE" 8010)
FRONTEND_PORT=$(choose_port "${AGENTSWATCH_LOCAL_FRONTEND_PORT:-}" "$FRONTEND_PORT_FILE" 4173)
OBSERVABILITY_PORT=$(choose_port "${AGENTSWATCH_LOCAL_OBSERVABILITY_PORT:-}" "$OBSERVABILITY_PORT_FILE" 8011)

if [[ -n "${AGENTSWATCH_LOCAL_DB_PORT:-}" ]]; then
  DB_PORT=$AGENTSWATCH_LOCAL_DB_PORT
elif [[ -f "$DB_PORT_FILE" ]]; then
  DB_PORT=$(cat "$DB_PORT_FILE")
else
  DB_PORT=$(free_port)
fi

LOCAL_ADMIN_DATABASE_URL="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:$DB_PORT/agentswatch_local"
LOCAL_READER_DATABASE_URL="postgresql://agentswatch_reader:$READER_PASSWORD@127.0.0.1:$DB_PORT/agentswatch_local"
LOCAL_WRITER_DATABASE_URL="postgresql://agentswatch_app:$WRITER_PASSWORD@127.0.0.1:$DB_PORT/agentswatch_local"
LOCAL_OBSERVABILITY_DATABASE_URL="postgresql://agentswatch_observability:$OBSERVABILITY_DATABASE_PASSWORD@127.0.0.1:$DB_PORT/agentswatch_local"

compose() {
  AGENTSWATCH_LOCAL_DB_PORT="$DB_PORT" \
    AGENTSWATCH_LOCAL_POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    docker compose -f "$COMPOSE_FILE" "$@"
}

pid_is_running() {
  local file=$1
  [[ -f "$file" ]] || return 1
  local pid
  pid=$(cat "$file")
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1
}

stop_pid() {
  local file=$1
  if pid_is_running "$file"; then
    local pid
    pid=$(cat "$file")
    kill "$pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
      kill -0 "$pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$file"
}

wait_for_url() {
  local url=$1
  local label=$2
  for _ in $(seq 1 100); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  echo "$label did not become ready: $url" >&2
  return 1
}

port_is_listening() {
  local port=$1
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

ensure_api_runtime() {
  if [[ ! -x "$ROOT/.venv-api/bin/python" ]]; then
    python3 -m venv "$ROOT/.venv-api"
  fi
  if ! "$ROOT/.venv-api/bin/python" -c 'import ansible_runner, cryptography, fastapi, psycopg' >/dev/null 2>&1; then
    "$ROOT/.venv-api/bin/pip" install -r "$ROOT/requirements-api.txt"
  fi
  if ! "$ROOT/.venv-api/bin/python" -c 'import mcp, httpx' >/dev/null 2>&1; then
    "$ROOT/.venv-api/bin/pip" install -r "$ROOT/requirements-mcp.txt"
  fi
  if ! "$ROOT/.venv-api/bin/python" -c 'import yaml' >/dev/null 2>&1; then
    "$ROOT/.venv-api/bin/pip" install -r "$ROOT/requirements-observability-control.txt"
  fi
}

ensure_web_credential_key() {
  if [[ ! -s "$WEB_CREDENTIAL_KEY_FILE" ]]; then
    python3 - <<'PY' > "$WEB_CREDENTIAL_KEY_FILE"
import base64
import secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
    chmod 600 "$WEB_CREDENTIAL_KEY_FILE"
  fi
}

ensure_ssh_credentials() {
  if [[ ! -s "$SSH_CREDENTIAL_KEY_FILE" ]]; then
    python3 - <<'PY' > "$SSH_CREDENTIAL_KEY_FILE"
import base64
import secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
    chmod 600 "$SSH_CREDENTIAL_KEY_FILE"
  fi
  if [[ ! -s "$MANAGED_SSH_PRIVATE_KEY_FILE" ]]; then
    ssh-keygen -q -t ed25519 -N '' -C agentswatch-managed -f "$MANAGED_SSH_PRIVATE_KEY_FILE"
  fi
  chmod 600 "$MANAGED_SSH_PRIVATE_KEY_FILE"
  chmod 644 "$MANAGED_SSH_PRIVATE_KEY_FILE.pub"
}

ensure_observability_token() {
  if [[ ! -s "$OBSERVABILITY_TOKEN_FILE" ]]; then
    "$ROOT/.venv-api/bin/python" - <<'PY' > "$OBSERVABILITY_TOKEN_FILE"
import secrets
print(secrets.token_urlsafe(32))
PY
    chmod 600 "$OBSERVABILITY_TOKEN_FILE"
  fi
}

ensure_frontend_runtime() {
  if [[ ! -x "$ROOT/frontend/node_modules/.bin/vite" ]]; then
    (cd "$ROOT/frontend" && npm install)
  fi
}

ensure_local_database_upgrade() {
  AGENTSWATCH_MIGRATION_DATABASE_URL="$LOCAL_ADMIN_DATABASE_URL" \
    "$ROOT/.venv-api/bin/python" "$ROOT/scripts/bootstrap_local_migration_ledger.py"
  AGENTSWATCH_MIGRATION_DATABASE_URL="$LOCAL_ADMIN_DATABASE_URL" \
    "$ROOT/.venv-api/bin/python" "$ROOT/scripts/apply_core_asset_migrations.py"
  AGENTSWATCH_DATABASE_ADMIN_URL="$LOCAL_ADMIN_DATABASE_URL" \
    AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE=postgres \
    AGENTSWATCH_DATABASE_READER_ROLE=agentswatch_reader \
    AGENTSWATCH_DATABASE_READER_PASSWORD="$READER_PASSWORD" \
    AGENTSWATCH_DATABASE_WRITER_ROLE=agentswatch_app \
    AGENTSWATCH_DATABASE_WRITER_PASSWORD="$WRITER_PASSWORD" \
    "$ROOT/.venv-api/bin/python" "$ROOT/scripts/configure_core_asset_roles.py"
  AGENTSWATCH_DATABASE_ADMIN_URL="$LOCAL_ADMIN_DATABASE_URL" \
    AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE=postgres \
    AGENTSWATCH_OBSERVABILITY_DATABASE_ROLE=agentswatch_observability \
    AGENTSWATCH_OBSERVABILITY_DATABASE_PASSWORD="$OBSERVABILITY_DATABASE_PASSWORD" \
    "$ROOT/.venv-api/bin/python" "$ROOT/scripts/configure_observability_integration_role.py"
}

ensure_local_admin() {
  local username="${AGENTSWATCH_LOCAL_ADMIN_USERNAME:-admin}"
  local user_count
  user_count=$(AGENTSWATCH_WRITE_DATABASE_URL="$LOCAL_WRITER_DATABASE_URL" "$ROOT/.venv-api/bin/python" - <<'PY'
from agentswatch_api.database import WriteDatabase
with WriteDatabase().connection() as connection:
    print(connection.execute("SELECT count(*) AS count FROM security.user_account").fetchone()["count"])
PY
)
  if [[ "$user_count" == "0" ]]; then
    printf '%s\n' "$ADMIN_PASSWORD" | AGENTSWATCH_WRITE_DATABASE_URL="$LOCAL_WRITER_DATABASE_URL" \
      "$ROOT/.venv-api/bin/python" "$ROOT/scripts/manage_auth_user.py" \
      --username "$username" --display-name "本机管理员" --role admin --password-stdin >/dev/null
  fi
}

start_stack() {
  command -v docker >/dev/null
  command -v curl >/dev/null
  command -v lsof >/dev/null
  command -v npm >/dev/null

  echo "$DB_PORT" > "$DB_PORT_FILE"
  echo "$API_PORT" > "$API_PORT_FILE"
  echo "$MCP_PORT" > "$MCP_PORT_FILE"
  echo "$FRONTEND_PORT" > "$FRONTEND_PORT_FILE"
  echo "$OBSERVABILITY_PORT" > "$OBSERVABILITY_PORT_FILE"
  echo $$ > "$SUPERVISOR_PID_FILE"
  compose up -d --wait core-db-local
  ensure_api_runtime
  ensure_local_database_upgrade
  ensure_frontend_runtime
  ensure_web_credential_key
  ensure_ssh_credentials
  ensure_observability_token
  ensure_local_admin

  if ! curl -fsS "http://127.0.0.1:$OBSERVABILITY_PORT/healthz" >/dev/null 2>&1; then
    if port_is_listening "$OBSERVABILITY_PORT"; then
      echo "port $OBSERVABILITY_PORT is already used by another service" >&2
      exit 1
    fi
    AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT="$OBSERVABILITY_STATE_ROOT" \
      AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL="$LOCAL_OBSERVABILITY_DATABASE_URL" \
      AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN="$(cat "$OBSERVABILITY_TOKEN_FILE")" \
      AGENTSWATCH_OTEL_ENDPOINT="${AGENTSWATCH_LOCAL_OTEL_ENDPOINT:-http://127.0.0.1:4318}" \
      AGENTSWATCH_PROMETHEUS_RELOAD_URL="${AGENTSWATCH_PROMETHEUS_RELOAD_URL:-}" \
      "$ROOT/.venv-api/bin/python" -m agentswatch_observability serve \
      --host 127.0.0.1 --port "$OBSERVABILITY_PORT" >"$OBSERVABILITY_LOG" 2>&1 &
    echo $! > "$OBSERVABILITY_PID_FILE"
  fi
  if ! wait_for_url "http://127.0.0.1:$OBSERVABILITY_PORT/healthz" "observability control adapter"; then
    sed -n '1,120p' "$OBSERVABILITY_LOG" >&2 || true
    exit 1
  fi

  if ! curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then
    if port_is_listening "$API_PORT"; then
      echo "port $API_PORT is already used by another service" >&2
      exit 1
    fi
    AGENTSWATCH_DATABASE_URL="$LOCAL_READER_DATABASE_URL" \
      AGENTSWATCH_WRITE_DATABASE_URL="$LOCAL_WRITER_DATABASE_URL" \
      AGENTSWATCH_ENABLE_MANAGEMENT=true \
      AGENTSWATCH_ALLOW_LOCAL_CONNECTION=true \
      AGENTSWATCH_WEB_CREDENTIAL_KEY="$(cat "$WEB_CREDENTIAL_KEY_FILE")" \
      AGENTSWATCH_SSH_CREDENTIAL_KEY="$(cat "$SSH_CREDENTIAL_KEY_FILE")" \
      AGENTSWATCH_DEFAULT_SSH_CREDENTIAL_REF=agentswatch-managed \
      AGENTSWATCH_DEFAULT_SSH_PRIVATE_KEY_FILE="$MANAGED_SSH_PRIVATE_KEY_FILE" \
      AGENTSWATCH_OBSERVABILITY_CONTROL_URL="http://127.0.0.1:$OBSERVABILITY_PORT" \
      AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN="$(cat "$OBSERVABILITY_TOKEN_FILE")" \
      AGENTSWATCH_WEB_ACCESS_TTL_SECONDS="${AGENTSWATCH_WEB_ACCESS_TTL_SECONDS:-900}" \
      AGENTSWATCH_SESSION_COOKIE_SECURE=false \
      "$ROOT/.venv-api/bin/python" "$ROOT/scripts/run_core_api.py" --port "$API_PORT" \
      >"$API_LOG" 2>&1 &
    echo $! > "$API_PID_FILE"
  fi
  if ! wait_for_url "http://127.0.0.1:$API_PORT/healthz" "core API"; then
    sed -n '1,120p' "$API_LOG" >&2 || true
    exit 1
  fi

  "$ROOT/.venv-api/bin/python" - "$API_PORT" <<'PY'
import json
import sys
from urllib.request import urlopen

with urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=3) as response:
    payload = json.load(response)
assert payload["status"] == "ok"
assert payload["database_user"] == "agentswatch_reader"
assert payload["transaction_read_only"] == "on"
PY

  if ! curl -fsS "http://127.0.0.1:$MCP_PORT/healthz" >/dev/null 2>&1; then
    if port_is_listening "$MCP_PORT"; then
      echo "port $MCP_PORT is already used by another service" >&2
      exit 1
    fi
    AGENTSWATCH_API_BASE_URL="http://127.0.0.1:$API_PORT" \
      AGENTSWATCH_MCP_HOST=127.0.0.1 \
      AGENTSWATCH_MCP_PORT="$MCP_PORT" \
      "$ROOT/.venv-api/bin/python" -m agentswatch_mcp >"$MCP_LOG" 2>&1 &
    echo $! > "$MCP_PID_FILE"
  fi
  if ! wait_for_url "http://127.0.0.1:$MCP_PORT/healthz" "MCP service"; then
    sed -n '1,120p' "$MCP_LOG" >&2 || true
    exit 1
  fi

  if [[ "${AGENTSWATCH_ADOPT_LOCAL_OPENCLAW:-true}" == "true" ]]; then
    if ! AGENTSWATCH_API_USERNAME="${AGENTSWATCH_LOCAL_ADMIN_USERNAME:-admin}" \
      AGENTSWATCH_API_PASSWORD="$ADMIN_PASSWORD" \
      AGENTSWATCH_WRITE_DATABASE_URL="$LOCAL_WRITER_DATABASE_URL" \
      "$ROOT/.venv-api/bin/python" "$ROOT/scripts/adopt_local_openclaw.py" \
      --api-url "http://127.0.0.1:$API_PORT" >"$LOCAL_ADOPTION_LOG" 2>&1; then
      sed -n '1,120p' "$LOCAL_ADOPTION_LOG" >&2
      exit 1
    fi
    sed -n '1,20p' "$LOCAL_ADOPTION_LOG"
  fi

  if ! curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" 2>/dev/null | rg -q 'AgentsWatch'; then
    if port_is_listening "$FRONTEND_PORT"; then
      echo "port $FRONTEND_PORT is already used by another service" >&2
      exit 1
    fi
    tail -f /dev/null | AGENTSWATCH_LOCAL_API_PORT="$API_PORT" "$ROOT/frontend/node_modules/.bin/vite" "$ROOT/frontend" \
      --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort \
      >"$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
  fi
  cleanup_supervised() {
    trap - EXIT INT TERM
    stop_pid "$FRONTEND_PID_FILE"
    stop_pid "$MCP_PID_FILE"
    stop_pid "$API_PID_FILE"
    stop_pid "$OBSERVABILITY_PID_FILE"
    compose stop >/dev/null 2>&1 || true
    rm -f "$SUPERVISOR_PID_FILE"
  }
  trap cleanup_supervised EXIT INT TERM

  (
    if wait_for_url "http://127.0.0.1:$FRONTEND_PORT/" "frontend"; then
      echo "AgentsWatch local management stack is ready"
      echo "frontend: http://localhost:$FRONTEND_PORT/"
      echo "API docs: http://localhost:$API_PORT/docs"
      echo "MCP endpoint: http://localhost:$MCP_PORT/mcp"
      echo "observability control: http://localhost:$OBSERVABILITY_PORT"
      echo "database: 127.0.0.1:$DB_PORT/agentswatch_local (reader: agentswatch_reader, writer: agentswatch_app)"
      echo "login user: ${AGENTSWATCH_LOCAL_ADMIN_USERNAME:-admin}"
      if [[ -n "${AGENTSWATCH_LOCAL_ADMIN_PASSWORD:-}" ]]; then
        echo "login password: supplied through AGENTSWATCH_LOCAL_ADMIN_PASSWORD"
      else
        echo "login password file: $ADMIN_PASSWORD_FILE"
      fi
      echo "logs: $LOCAL_DIR"
      echo "press Ctrl+C to stop the local stack"
    else
      sed -n '1,120p' "$FRONTEND_LOG" >&2 || true
      kill -TERM "$(cat "$SUPERVISOR_PID_FILE")" >/dev/null 2>&1 || true
    fi
  ) &

  wait "$(cat "$FRONTEND_PID_FILE")"
}

stop_stack() {
  if pid_is_running "$SUPERVISOR_PID_FILE"; then
    local supervisor_pid
    supervisor_pid=$(cat "$SUPERVISOR_PID_FILE")
    kill "$supervisor_pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
      kill -0 "$supervisor_pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
  else
    stop_pid "$FRONTEND_PID_FILE"
    stop_pid "$MCP_PID_FILE"
    stop_pid "$API_PID_FILE"
    stop_pid "$OBSERVABILITY_PID_FILE"
    compose stop >/dev/null 2>&1 || true
  fi
  rm -f "$SUPERVISOR_PID_FILE"
  echo "AgentsWatch local management stack stopped"
}

status_stack() {
  local failed=0
  if compose ps --status running core-db-local | rg -q 'core-db-local'; then
    echo "database: running"
  else
    echo "database: stopped"
    failed=1
  fi
  if curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then
    echo "API: running on http://localhost:$API_PORT"
  else
    echo "API: stopped"
    failed=1
  fi
  if curl -fsS "http://127.0.0.1:$MCP_PORT/healthz" >/dev/null 2>&1; then
    echo "MCP: running on http://localhost:$MCP_PORT/mcp"
  else
    echo "MCP: stopped"
    failed=1
  fi
  if curl -fsS "http://127.0.0.1:$OBSERVABILITY_PORT/healthz" >/dev/null 2>&1; then
    echo "observability control: running on http://localhost:$OBSERVABILITY_PORT"
  else
    echo "observability control: stopped"
    failed=1
  fi
  if curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" 2>/dev/null | rg -q 'AgentsWatch'; then
    echo "frontend: running on http://localhost:$FRONTEND_PORT"
  else
    echo "frontend: stopped"
    failed=1
  fi
  return "$failed"
}

reset_stack() {
  if pid_is_running "$SUPERVISOR_PID_FILE"; then
    kill "$(cat "$SUPERVISOR_PID_FILE")" >/dev/null 2>&1 || true
    sleep 0.3
  fi
  stop_pid "$FRONTEND_PID_FILE"
  stop_pid "$MCP_PID_FILE"
  stop_pid "$API_PID_FILE"
  stop_pid "$OBSERVABILITY_PID_FILE"
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$OBSERVABILITY_STATE_ROOT"
  rm -f "$API_LOG" "$MCP_LOG" "$FRONTEND_LOG" "$OBSERVABILITY_LOG" "$LOCAL_ADOPTION_LOG" "$DB_PORT_FILE" "$API_PORT_FILE" "$MCP_PORT_FILE" "$FRONTEND_PORT_FILE" "$OBSERVABILITY_PORT_FILE" "$SUPERVISOR_PID_FILE" "$POSTGRES_PASSWORD_FILE" "$READER_PASSWORD_FILE" "$WRITER_PASSWORD_FILE" "$OBSERVABILITY_DATABASE_PASSWORD_FILE" "$ADMIN_PASSWORD_FILE"
  echo "AgentsWatch local management stack reset"
}

case "$ACTION" in
  start) start_stack ;;
  stop) stop_stack ;;
  status) status_stack ;;
  reset) reset_stack ;;
  *)
    echo "usage: $0 {start|stop|status|reset}" >&2
    exit 2
    ;;
esac
