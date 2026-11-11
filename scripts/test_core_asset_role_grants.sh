#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-$ROOT/.venv-api/bin/python}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16-alpine}
POSTGRES_PLATFORM=${POSTGRES_PLATFORM:-linux/amd64}
CONTAINER_NAME="agentswatch-role-grants-test-$$"
DATABASE=agentswatch_role_grants_test
READER_ROLE="agentswatch-reader-test-$$"
WRITER_ROLE="agentswatch-writer-test-$$"
OBSERVABILITY_ROLE="agentswatch-observability-test-$$"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "API virtual environment is missing" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import psycopg' >/dev/null 2>&1; then
  echo "psycopg is missing from the API virtual environment" >&2
  exit 1
fi

POSTGRES_PASSWORD=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(24))')
READER_PASSWORD=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(24))')
WRITER_PASSWORD=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(24))')
OBSERVABILITY_PASSWORD=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(24))')
export POSTGRES_PASSWORD

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  --platform "$POSTGRES_PLATFORM" \
  -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD \
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

database_port=$(docker port "$CONTAINER_NAME" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')
ADMIN_URL="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${database_port}/${DATABASE}"
READER_URL="postgresql://${READER_ROLE}:${READER_PASSWORD}@127.0.0.1:${database_port}/${DATABASE}"
WRITER_URL="postgresql://${WRITER_ROLE}:${WRITER_PASSWORD}@127.0.0.1:${database_port}/${DATABASE}"
OBSERVABILITY_URL="postgresql://${OBSERVABILITY_ROLE}:${OBSERVABILITY_PASSWORD}@127.0.0.1:${database_port}/${DATABASE}"

AGENTSWATCH_MIGRATION_DATABASE_URL="$ADMIN_URL" \
  "$PYTHON_BIN" "$ROOT/scripts/apply_core_asset_migrations.py" >/dev/null

export AGENTSWATCH_DATABASE_ADMIN_URL="$ADMIN_URL"
export AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE=postgres
export AGENTSWATCH_DATABASE_READER_ROLE="$READER_ROLE"
export AGENTSWATCH_DATABASE_READER_PASSWORD="$READER_PASSWORD"
export AGENTSWATCH_DATABASE_WRITER_ROLE="$WRITER_ROLE"
export AGENTSWATCH_DATABASE_WRITER_PASSWORD="$WRITER_PASSWORD"

first_output=$($PYTHON_BIN "$ROOT/scripts/configure_core_asset_roles.py")
second_output=$($PYTHON_BIN "$ROOT/scripts/configure_core_asset_roles.py")
for output in "$first_output" "$second_output"; do
  if [[ "$output" == *"$POSTGRES_PASSWORD"* || "$output" == *"$READER_PASSWORD"* || "$output" == *"$WRITER_PASSWORD"* ]]; then
    echo "role configuration output exposed a password" >&2
    exit 1
  fi
done

export AGENTSWATCH_OBSERVABILITY_DATABASE_ROLE="$OBSERVABILITY_ROLE"
export AGENTSWATCH_OBSERVABILITY_DATABASE_PASSWORD="$OBSERVABILITY_PASSWORD"
observability_first_output=$($PYTHON_BIN "$ROOT/scripts/configure_observability_integration_role.py")
observability_second_output=$($PYTHON_BIN "$ROOT/scripts/configure_observability_integration_role.py")
for output in "$observability_first_output" "$observability_second_output"; do
  if [[ "$output" == *"$POSTGRES_PASSWORD"* || "$output" == *"$OBSERVABILITY_PASSWORD"* ]]; then
    echo "observability role configuration output exposed a password" >&2
    exit 1
  fi
done

AGENTSWATCH_ROLE_GRANT_TEST_ADMIN_URL="$ADMIN_URL" \
  AGENTSWATCH_ROLE_GRANT_TEST_READER_URL="$READER_URL" \
  AGENTSWATCH_ROLE_GRANT_TEST_WRITER_URL="$WRITER_URL" \
  AGENTSWATCH_ROLE_GRANT_TEST_READER_ROLE="$READER_ROLE" \
  AGENTSWATCH_ROLE_GRANT_TEST_WRITER_ROLE="$WRITER_ROLE" \
  "$PYTHON_BIN" -m unittest tests.test_core_asset_role_grants_integration -v

AGENTSWATCH_OBSERVABILITY_ROLE_TEST_URL="$OBSERVABILITY_URL" \
  "$PYTHON_BIN" -m unittest tests.test_observability_integration_role -v

echo "isolated PostgreSQL API role grant test passed"
