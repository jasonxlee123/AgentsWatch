#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-$ROOT/.venv-api/bin/python}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16-alpine}
POSTGRES_PLATFORM=${POSTGRES_PLATFORM:-linux/amd64}
CONTAINER_NAME="agentswatch-local-upgrade-test-$$"
DATABASE=agentswatch_local
POSTGRES_PASSWORD=$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(24))')
READER_PASSWORD=$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(24))')
WRITER_PASSWORD=$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(24))')

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  --platform "$POSTGRES_PLATFORM" \
  -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e POSTGRES_DB="$DATABASE" \
  "$POSTGRES_IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc 'select 1' >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

for migration in "$ROOT"/database/core-assets/*.sql; do
  filename=$(basename "$migration")
  case "$filename" in
    003_local_reader.sql|014_organization_master_guards.sql|015_runtime_primary_agent_one_to_one.sql|016_runtime_management_groups.sql|017_server_password_bootstrap.sql|018_api_keys.sql|019_operation_confirmations.sql|020_observability_gateway_v2.sql|021_agent_deployment_primary_role_integrity.sql) continue ;;
  esac
  docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" \
    < "$migration" >/dev/null
done
docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" <<'SQL' >/dev/null
CREATE ROLE agentswatch_reader NOLOGIN;
GRANT CONNECT ON DATABASE agentswatch_local TO agentswatch_reader;
GRANT USAGE ON SCHEMA asset, org, audit, ingest, automation TO agentswatch_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA asset, org, audit, ingest, automation TO agentswatch_reader;
ALTER ROLE agentswatch_reader SET default_transaction_read_only = on;

CREATE ROLE agentswatch_app NOLOGIN;
GRANT CONNECT ON DATABASE agentswatch_local TO agentswatch_app;
GRANT USAGE ON SCHEMA asset, org, audit, automation TO agentswatch_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA asset, audit, automation TO agentswatch_app;
GRANT SELECT ON ALL TABLES IN SCHEMA org TO agentswatch_app;
SQL
docker exec "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" -c \
  "INSERT INTO org.department (department_code, name) VALUES ('LOCAL-UPGRADE-SENTINEL', '升级保留验证')" \
  >/dev/null

port=$(docker port "$CONTAINER_NAME" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')
admin_url="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:$port/$DATABASE"

upgrade() {
  AGENTSWATCH_MIGRATION_DATABASE_URL="$admin_url" \
    "$PYTHON_BIN" "$ROOT/scripts/bootstrap_local_migration_ledger.py" >/dev/null
  AGENTSWATCH_MIGRATION_DATABASE_URL="$admin_url" \
    "$PYTHON_BIN" "$ROOT/scripts/apply_core_asset_migrations.py" >/dev/null
  AGENTSWATCH_DATABASE_ADMIN_URL="$admin_url" \
    AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE=postgres \
    AGENTSWATCH_DATABASE_READER_ROLE=agentswatch_reader \
    AGENTSWATCH_DATABASE_READER_PASSWORD="$READER_PASSWORD" \
    AGENTSWATCH_DATABASE_WRITER_ROLE=agentswatch_app \
    AGENTSWATCH_DATABASE_WRITER_PASSWORD="$WRITER_PASSWORD" \
    "$PYTHON_BIN" "$ROOT/scripts/configure_core_asset_roles.py" >/dev/null
}

upgrade
upgrade

result=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc "
SELECT
  (SELECT count(*) FROM public.agentswatch_schema_migration),
  (SELECT max(version) FROM public.agentswatch_schema_migration),
  (SELECT count(*) FROM org.department WHERE department_code = 'LOCAL-UPGRADE-SENTINEL'),
  (SELECT count(*) FROM pg_trigger
   WHERE tgname IN ('trg_department_identity_guard', 'trg_person_identity_guard')
     AND NOT tgisinternal),
  has_table_privilege('agentswatch_app', 'org.department', 'INSERT'),
  has_table_privilege('agentswatch_app', 'org.department', 'UPDATE'),
  has_table_privilege('agentswatch_app', 'org.department', 'DELETE'),
  has_table_privilege('agentswatch_reader', 'integration.observability_gateway_v1', 'SELECT'),
  has_table_privilege('agentswatch_reader', 'integration.observability_gateway_v2', 'SELECT'),
  to_regclass('asset.uq_agent_identity_structure_role') IS NOT NULL;
")
if [[ "$result" != "20|021|1|2|t|t|f|t|t|t" ]]; then
  echo "unexpected persistent-upgrade state: $result" >&2
  exit 1
fi

"$PYTHON_BIN" - "postgresql://agentswatch_app:$WRITER_PASSWORD@127.0.0.1:$port/$DATABASE" <<'PY'
import sys
import psycopg

with psycopg.connect(sys.argv[1]) as connection:
    connection.execute(
        "UPDATE org.department SET name = '升级后可写' "
        "WHERE department_code = 'LOCAL-UPGRADE-SENTINEL'"
    )
try:
    with psycopg.connect(sys.argv[1]) as connection:
        connection.execute(
            "DELETE FROM org.department WHERE department_code = 'LOCAL-UPGRADE-SENTINEL'"
        )
except psycopg.Error:
    pass
else:
    raise AssertionError("local writer unexpectedly deleted organization master data")
PY

echo "legacy local persistent database upgraded in place and idempotently"
