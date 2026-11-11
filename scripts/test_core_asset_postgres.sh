#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16-alpine}
POSTGRES_PLATFORM=${POSTGRES_PLATFORM:-linux/amd64}
CONTAINER_NAME="agentswatch-core-data-test-$$"
DATABASE=agentswatch_test
PASSWORD=local-test-only
TMP_DIR=$(mktemp -d)

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  --network none \
  --platform "$POSTGRES_PLATFORM" \
  -e POSTGRES_PASSWORD="$PASSWORD" \
  -e POSTGRES_DB="$DATABASE" \
  "$POSTGRES_IMAGE" >/dev/null

ready=false
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" \
    psql -U postgres -d "$DATABASE" -Atc 'select 1' >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 0.2
done
if [[ "$ready" != true ]]; then
  echo "temporary PostgreSQL did not become ready" >&2
  exit 1
fi

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/001_core_asset_schema.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/002_server_management.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/004_runtime_management.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/005_runtime_web_access.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/006_agent_channels.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/007_subagent_channels.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/008_agent_operations.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/009_automation_library_and_tasks.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/010_operation_events.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/011_allow_shared_public_ip.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/012_systemd_user_runtime_management.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/013_user_authentication.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/014_organization_master_guards.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/015_runtime_primary_agent_one_to_one.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/016_runtime_management_groups.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/017_server_password_bootstrap.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/018_api_keys.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/019_operation_confirmations.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/020_observability_gateway_v2.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/database/core-assets/021_agent_deployment_primary_role_integrity.sql" >/dev/null

docker exec -i "$CONTAINER_NAME" \
  psql -U postgres -d "$DATABASE" \
  < "$ROOT/tests/sql/core_asset_schema_test.sql"

docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" <<'SQL' >/dev/null
INSERT INTO asset.runtime_instance (
    runtime_id, server_id, runtime_family_id, name, deployment_mode,
    lifecycle_status, observed_status, endpoint_port, runtime_account
) VALUES
    (
        '00000000-0000-4000-8000-000000009921',
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000601',
        '并发部署验证运行时一', 'baremetal', 'active', 'running', 19921, 'openclaw'
    ),
    (
        '00000000-0000-4000-8000-000000009922',
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000601',
        '并发部署验证运行时二', 'baremetal', 'active', 'running', 19922, 'openclaw'
    ),
    (
        '00000000-0000-4000-8000-000000009923',
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000601',
        '并发运行时唯一性验证', 'baremetal', 'active', 'running', 19923, 'openclaw'
    ),
    (
        '00000000-0000-4000-8000-000000009924',
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000601',
        '并发角色完整性验证', 'baremetal', 'active', 'running', 19924, 'openclaw'
    );

INSERT INTO asset.agent_asset (
    agent_id, name, structure_role, service_archetype, lifecycle_status,
    owning_department_id, business_admin_person_id, it_admin_person_id,
    business_domain, business_scenario
) VALUES
    (
        '00000000-0000-4000-8000-000000009901',
        '并发部署验证智能体', 'primary', 'business', 'active',
        '00000000-0000-4000-8000-000000000203',
        '00000000-0000-4000-8000-000000000301',
        '00000000-0000-4000-8000-000000000302',
        '并发验证', '活动部署唯一性'
    ),
    (
        '00000000-0000-4000-8000-000000009902',
        '并发运行时验证智能体一', 'primary', 'business', 'active',
        '00000000-0000-4000-8000-000000000203',
        '00000000-0000-4000-8000-000000000301',
        '00000000-0000-4000-8000-000000000302',
        '并发验证', '运行时唯一性一'
    ),
    (
        '00000000-0000-4000-8000-000000009903',
        '并发运行时验证智能体二', 'primary', 'business', 'active',
        '00000000-0000-4000-8000-000000000203',
        '00000000-0000-4000-8000-000000000301',
        '00000000-0000-4000-8000-000000000302',
        '并发验证', '运行时唯一性二'
    ),
    (
        '00000000-0000-4000-8000-000000009904',
        '并发角色变更验证智能体', 'primary', 'business', 'active',
        '00000000-0000-4000-8000-000000000203',
        '00000000-0000-4000-8000-000000000301',
        '00000000-0000-4000-8000-000000000302',
        '并发验证', '角色与部署完整性'
    );
SQL

first_sql="BEGIN;
INSERT INTO asset.agent_deployment (
  agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
) VALUES (
  '00000000-0000-4000-8000-000000009911',
  '00000000-0000-4000-8000-000000009901',
  '00000000-0000-4000-8000-000000009921',
  'active'
);
SELECT pg_sleep(1.5);
COMMIT;"

second_sql="BEGIN;
INSERT INTO asset.agent_deployment (
  agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
) VALUES (
  '00000000-0000-4000-8000-000000009912',
  '00000000-0000-4000-8000-000000009901',
  '00000000-0000-4000-8000-000000009922',
  'active'
);
COMMIT;"

docker exec "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c "$first_sql" \
  >"$TMP_DIR/first.log" 2>&1 &
first_pid=$!
sleep 0.2
docker exec "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c "$second_sql" \
  >"$TMP_DIR/second.log" 2>&1 &
second_pid=$!

set +e
wait "$first_pid"
first_status=$?
wait "$second_pid"
second_status=$?
set -e

if [[ "$first_status" -eq 0 && "$second_status" -ne 0 ]]; then
  failed_log="$TMP_DIR/second.log"
elif [[ "$first_status" -ne 0 && "$second_status" -eq 0 ]]; then
  failed_log="$TMP_DIR/first.log"
else
  echo "expected exactly one concurrent active deployment to commit" >&2
  sed -n '1,80p' "$TMP_DIR/first.log" >&2
  sed -n '1,80p' "$TMP_DIR/second.log" >&2
  exit 1
fi

if ! rg -q 'duplicate key value violates unique constraint "uq_agent_one_active_deployment"' "$failed_log"; then
  echo "concurrent loser did not fail on the expected partial unique index" >&2
  sed -n '1,80p' "$failed_log" >&2
  exit 1
fi

active_count=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc "
  SELECT count(*)
  FROM asset.agent_deployment
  WHERE primary_agent_id = '00000000-0000-4000-8000-000000009901'
    AND lifecycle_status = 'active';
")
if [[ "$active_count" != "1" ]]; then
  echo "expected one committed active deployment, received $active_count" >&2
  exit 1
fi

runtime_first_sql="BEGIN;
INSERT INTO asset.agent_deployment (
  agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
) VALUES (
  '00000000-0000-4000-8000-000000009913',
  '00000000-0000-4000-8000-000000009902',
  '00000000-0000-4000-8000-000000009923',
  'active'
);
SELECT pg_sleep(1.5);
COMMIT;"

runtime_second_sql="BEGIN;
INSERT INTO asset.agent_deployment (
  agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
) VALUES (
  '00000000-0000-4000-8000-000000009914',
  '00000000-0000-4000-8000-000000009903',
  '00000000-0000-4000-8000-000000009923',
  'active'
);
COMMIT;"

docker exec "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c "$runtime_first_sql" \
  >"$TMP_DIR/runtime-first.log" 2>&1 &
runtime_first_pid=$!
sleep 0.2
docker exec "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c "$runtime_second_sql" \
  >"$TMP_DIR/runtime-second.log" 2>&1 &
runtime_second_pid=$!

set +e
wait "$runtime_first_pid"
runtime_first_status=$?
wait "$runtime_second_pid"
runtime_second_status=$?
set -e

if [[ "$runtime_first_status" -eq 0 && "$runtime_second_status" -ne 0 ]]; then
  runtime_failed_log="$TMP_DIR/runtime-second.log"
elif [[ "$runtime_first_status" -ne 0 && "$runtime_second_status" -eq 0 ]]; then
  runtime_failed_log="$TMP_DIR/runtime-first.log"
else
  echo "expected exactly one concurrent primary Agent to bind to the Runtime" >&2
  sed -n '1,80p' "$TMP_DIR/runtime-first.log" >&2
  sed -n '1,80p' "$TMP_DIR/runtime-second.log" >&2
  exit 1
fi

if ! rg -q 'duplicate key value violates unique constraint "uq_runtime_one_active_primary_agent"' "$runtime_failed_log"; then
  echo "concurrent Runtime-binding loser did not fail on the expected partial unique index" >&2
  sed -n '1,80p' "$runtime_failed_log" >&2
  exit 1
fi

runtime_active_count=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc "
  SELECT count(*)
  FROM asset.agent_deployment
  WHERE runtime_id = '00000000-0000-4000-8000-000000009923'
    AND lifecycle_status = 'active';
")
if [[ "$runtime_active_count" != "1" ]]; then
  echo "expected one committed active primary Agent for the Runtime, received $runtime_active_count" >&2
  exit 1
fi

role_update_sql="BEGIN;
UPDATE asset.agent_asset
SET structure_role = 'subagent',
    parent_agent_id = '00000000-0000-4000-8000-000000009902',
    business_admin_person_id = NULL,
    it_admin_person_id = NULL,
    business_domain = NULL,
    business_scenario = NULL
WHERE agent_id = '00000000-0000-4000-8000-000000009904';
SELECT pg_sleep(1.5);
COMMIT;"

role_insert_sql="BEGIN;
INSERT INTO asset.agent_deployment (
  agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
) VALUES (
  '00000000-0000-4000-8000-000000009915',
  '00000000-0000-4000-8000-000000009904',
  '00000000-0000-4000-8000-000000009924',
  'active'
);
COMMIT;"

docker exec "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c "$role_update_sql" \
  >"$TMP_DIR/role-update.log" 2>&1 &
role_update_pid=$!
sleep 0.2
docker exec "$CONTAINER_NAME" \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c "$role_insert_sql" \
  >"$TMP_DIR/role-insert.log" 2>&1 &
role_insert_pid=$!

set +e
wait "$role_update_pid"
role_update_status=$?
wait "$role_insert_pid"
role_insert_status=$?
set -e

if [[ "$role_update_status" -ne 0 || "$role_insert_status" -eq 0 ]]; then
  echo "expected the serialized Agent role change to win and Deployment insert to fail" >&2
  sed -n '1,80p' "$TMP_DIR/role-update.log" >&2
  sed -n '1,80p' "$TMP_DIR/role-insert.log" >&2
  exit 1
fi
if ! rg -q 'violates foreign key constraint "fk_agent_deployment_primary_role"' "$TMP_DIR/role-insert.log"; then
  echo "concurrent Deployment insert did not fail on the primary-role foreign key" >&2
  sed -n '1,80p' "$TMP_DIR/role-insert.log" >&2
  exit 1
fi

role_serialization_state=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc "
  SELECT
    (SELECT structure_role FROM asset.agent_asset
     WHERE agent_id = '00000000-0000-4000-8000-000000009904'),
    (SELECT count(*) FROM asset.agent_deployment
     WHERE primary_agent_id = '00000000-0000-4000-8000-000000009904');
")
if [[ "$role_serialization_state" != "subagent|0" ]]; then
  echo "unexpected serialized primary-role state: $role_serialization_state" >&2
  exit 1
fi

table_count=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc "
  SELECT count(*)
  FROM information_schema.tables
  WHERE table_schema IN ('asset', 'org', 'audit', 'ingest', 'automation');
")
if [[ "$table_count" != "32" ]]; then
  echo "expected 32 prototype tables, received $table_count" >&2
  exit 1
fi

echo "concurrent active deployment test passed"
echo "concurrent Runtime primary-Agent binding test passed"
echo "concurrent Agent role/deployment foreign-key serialization test passed"
echo "local PostgreSQL prototype passed: 32 managed tables"
