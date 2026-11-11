#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-$ROOT/.venv-api/bin/python}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16-alpine}
POSTGRES_PLATFORM=${POSTGRES_PLATFORM:-linux/amd64}
CONTAINER_NAME="agentswatch-api-test-$$"
DATABASE=agentswatch_api_test
READER_PASSWORD=local-reader-only
TMP_DIR=$(mktemp -d)
API_PID=""

cleanup() {
  if [[ -n "$API_PID" ]]; then
    kill "$API_PID" >/dev/null 2>&1 || true
    wait "$API_PID" >/dev/null 2>&1 || true
  fi
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "API virtual environment is missing; run: python3 -m venv .venv-api && .venv-api/bin/pip install -r requirements-api.txt" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import cryptography, fastapi, openpyxl, psycopg, multipart' >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install -r "$ROOT/requirements-api.txt"
fi

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  --platform "$POSTGRES_PLATFORM" \
  -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD=local-test-only \
  -e POSTGRES_DB="$DATABASE" \
  "$POSTGRES_IMAGE" >/dev/null

ready=false
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc 'select 1' >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 0.2
done
if [[ "$ready" != true ]]; then
  echo "temporary PostgreSQL did not become ready" >&2
  exit 1
fi

docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/001_core_asset_schema.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/002_server_management.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/004_runtime_management.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/005_runtime_web_access.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/006_agent_channels.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/007_subagent_channels.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/008_agent_operations.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/009_automation_library_and_tasks.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/010_operation_events.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/011_allow_shared_public_ip.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/012_systemd_user_runtime_management.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/013_user_authentication.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/014_organization_master_guards.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/015_runtime_primary_agent_one_to_one.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/016_runtime_management_groups.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/017_server_password_bootstrap.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/018_api_keys.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/019_operation_confirmations.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/020_observability_gateway_v2.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/021_agent_deployment_primary_role_integrity.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" \
  < "$ROOT/tests/sql/core_asset_schema_test.sql" >/dev/null
docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" <<SQL >/dev/null
CREATE ROLE agentswatch_reader LOGIN PASSWORD '$READER_PASSWORD';
GRANT CONNECT ON DATABASE $DATABASE TO agentswatch_reader;
GRANT USAGE ON SCHEMA asset, org, audit, ingest, automation, integration TO agentswatch_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA asset, org, audit, ingest, automation, integration TO agentswatch_reader;
ALTER ROLE agentswatch_reader SET default_transaction_read_only = on;
CREATE ROLE agentswatch_app LOGIN PASSWORD 'local-app-only';
GRANT CONNECT ON DATABASE $DATABASE TO agentswatch_app;
GRANT USAGE ON SCHEMA asset, org, audit, ingest, automation, integration TO agentswatch_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA asset, audit, ingest, automation TO agentswatch_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA org TO agentswatch_app;
GRANT SELECT ON ALL TABLES IN SCHEMA integration TO agentswatch_app;
GRANT USAGE ON SCHEMA security TO agentswatch_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA security TO agentswatch_app;
INSERT INTO asset.server_connection (
  server_id, connection_type, host
) VALUES (
  '00000000-0000-4000-8000-000000000501', 'local', '127.0.0.1'
);
SQL

database_port=$(docker port "$CONTAINER_NAME" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')
api_port=$(
  "$PYTHON_BIN" - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)
database_url="postgresql://agentswatch_reader:$READER_PASSWORD@127.0.0.1:$database_port/$DATABASE"
write_database_url="postgresql://agentswatch_app:local-app-only@127.0.0.1:$database_port/$DATABASE"
web_credential_key=$(
  "$PYTHON_BIN" - <<'PY'
import base64
import secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
)
ssh_credential_key="$web_credential_key"

printf '%s\n' 'AgentsWatch!Test2026' | \
  AGENTSWATCH_WRITE_DATABASE_URL="$write_database_url" \
  "$PYTHON_BIN" "$ROOT/scripts/manage_auth_user.py" \
  --username integration-admin --display-name '集成测试管理员' --role admin --password-stdin >/dev/null

AGENTSWATCH_DATABASE_URL="$database_url" \
  AGENTSWATCH_WRITE_DATABASE_URL="$write_database_url" \
  AGENTSWATCH_ENABLE_MANAGEMENT=true \
  AGENTSWATCH_ALLOW_LOCAL_CONNECTION=true \
  AGENTSWATCH_WEB_CREDENTIAL_KEY="$web_credential_key" \
  AGENTSWATCH_SSH_CREDENTIAL_KEY="$ssh_credential_key" \
  AGENTSWATCH_WEB_ACCESS_TTL_SECONDS=30 \
  AGENTSWATCH_SESSION_COOKIE_SECURE=false \
  "$PYTHON_BIN" "$ROOT/scripts/run_core_api.py" --port "$api_port" \
  >"$TMP_DIR/api.log" 2>&1 &
API_PID=$!

api_ready=false
for _ in $(seq 1 60); do
  if "$PYTHON_BIN" - "$api_port" <<'PY' >/dev/null 2>&1
import sys
from urllib.request import urlopen
with urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=1) as response:
    assert response.status == 200
PY
  then
    api_ready=true
    break
  fi
  sleep 0.2
done
if [[ "$api_ready" != true ]]; then
  echo "local core API did not become ready" >&2
  sed -n '1,120p' "$TMP_DIR/api.log" >&2
  exit 1
fi

set +e
AGENTSWATCH_CORE_API_TEST_URL="http://127.0.0.1:$api_port" \
  AGENTSWATCH_CORE_API_TEST_DATABASE_URL="$write_database_url" \
  "$PYTHON_BIN" -m unittest \
    "${AGENTSWATCH_CORE_API_TEST_TARGET:-tests.test_core_api_integration}" -v
test_status=$?
set -e
if [[ "$test_status" -ne 0 ]]; then
  sed -n '1,240p' "$TMP_DIR/api.log" >&2
  exit "$test_status"
fi

secret_leaks=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc \
  "SELECT
      (SELECT count(*) FROM asset.agent_channel_configuration
       WHERE encrypted_app_secret IN ('super-secret-value', 'child-secret-value', 'deployment-primary-secret', 'deployment-child-secret')
          OR encrypted_app_secret LIKE '%super-secret-value%'
          OR encrypted_app_secret LIKE '%child-secret-value%'
          OR encrypted_app_secret LIKE '%deployment-primary-secret%'
          OR encrypted_app_secret LIKE '%deployment-child-secret%')
      +
      (SELECT count(*) FROM audit.change_log
       WHERE coalesce(before_value::text, '') LIKE '%super-secret-value%'
          OR coalesce(after_value::text, '') LIKE '%super-secret-value%'
          OR coalesce(before_value::text, '') LIKE '%child-secret-value%'
          OR coalesce(after_value::text, '') LIKE '%child-secret-value%'
          OR coalesce(before_value::text, '') LIKE '%deployment-primary-secret%'
          OR coalesce(after_value::text, '') LIKE '%deployment-primary-secret%'
          OR coalesce(before_value::text, '') LIKE '%deployment-child-secret%'
          OR coalesce(after_value::text, '') LIKE '%deployment-child-secret%')
      +
      (SELECT count(*) FROM automation.operation_run
       WHERE parameters::text LIKE '%deployment-primary-secret%'
          OR parameters::text LIKE '%deployment-child-secret%')
      +
      (SELECT count(*) FROM automation.operation_host_result
       WHERE coalesce(result::text, '') LIKE '%deployment-primary-secret%'
          OR coalesce(result::text, '') LIKE '%deployment-child-secret%'
          OR coalesce(target_plan::text, '') LIKE '%deployment-primary-secret%'
          OR coalesce(target_plan::text, '') LIKE '%deployment-child-secret%'
          OR coalesce(error_message, '') LIKE '%deployment-primary-secret%'
          OR coalesce(error_message, '') LIKE '%deployment-child-secret%')
      +
      (SELECT count(*) FROM automation.operation_event
       WHERE coalesce(stdout, '') LIKE '%deployment-primary-secret%'
          OR coalesce(stdout, '') LIKE '%deployment-child-secret%'
          OR event_data::text LIKE '%deployment-primary-secret%'
          OR event_data::text LIKE '%deployment-child-secret%')")
if [[ "$secret_leaks" != "0" ]]; then
  echo "agent AppSecret was stored without encryption" >&2
  exit 1
fi

set +e
docker exec -e PGPASSWORD="$READER_PASSWORD" "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U agentswatch_reader -d "$DATABASE" \
  -c "INSERT INTO asset.environment (environment_code, name, stage) VALUES ('forbidden', 'forbidden', 'test')" \
  >"$TMP_DIR/write-check.log" 2>&1
write_status=$?
set -e
if [[ "$write_status" -eq 0 ]] || ! rg -q 'read-only transaction' "$TMP_DIR/write-check.log"; then
  echo "database reader role unexpectedly allowed a write" >&2
  sed -n '1,80p' "$TMP_DIR/write-check.log" >&2
  exit 1
fi

echo "local read-only core API passed"
echo "database reader role rejected writes"
