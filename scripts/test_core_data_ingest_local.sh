#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16-alpine}
POSTGRES_PLATFORM=${POSTGRES_PLATFORM:-linux/amd64}
CONTAINER_NAME="agentswatch-ingest-test-$$"
DATABASE=agentswatch_ingest_test
TMP_DIR=$(mktemp -d)

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

python3 "$ROOT/scripts/preview_asset_import.py" \
  --input "$ROOT/tests/fixtures/core_asset_import_sample.xlsx" \
  --master "$ROOT/tests/fixtures/asset_import_master.json" \
  --output "$TMP_DIR/import-preview.json"
python3 "$ROOT/scripts/render_import_preview_sql.py" \
  --input "$TMP_DIR/import-preview.json" \
  --output "$TMP_DIR/import-preview.sql"
python3 "$ROOT/scripts/dry_run_core_asset_migration.py" \
  --output "$TMP_DIR/migration-preview.json"
python3 "$ROOT/scripts/preview_runtime_discovery.py" \
  --input "$ROOT/tests/fixtures/runtime_discovery/openclaw_shared_runtime.json" \
  --output "$TMP_DIR/openclaw-discovery.json"
python3 "$ROOT/scripts/preview_runtime_discovery.py" \
  --input "$ROOT/tests/fixtures/runtime_discovery/hermes_container_runtime.json" \
  --output "$TMP_DIR/hermes-discovery.json"

python3 - "$TMP_DIR" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
preview = json.loads((root / "import-preview.json").read_text())
migration = json.loads((root / "migration-preview.json").read_text())
openclaw = json.loads((root / "openclaw-discovery.json").read_text())
hermes = json.loads((root / "hermes-discovery.json").read_text())
assert preview["ready"] and preview["summary"]["rows"] == 5
assert preview["summary"]["status_counts"] == {"changed": 1, "new": 4}
assert migration["structurally_mappable"] and migration["staged_counts"]["servers"] == 33
assert openclaw["ok"] and openclaw["summary"]["primary_agents"] == 2
assert hermes["ok"] and hermes["runtime_family_code"] == "hermes"
PY

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  --network none \
  --platform "$POSTGRES_PLATFORM" \
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
docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" \
  < "$TMP_DIR/import-preview.sql" >/dev/null

counts=$(docker exec "$CONTAINER_NAME" psql -U postgres -d "$DATABASE" -Atc "
  SELECT
    (SELECT count(*) FROM ingest.import_batch) || ',' ||
    (SELECT count(*) FROM ingest.import_row) || ',' ||
    (SELECT count(*) FROM ingest.import_issue) || ',' ||
    (SELECT count(*) FROM ingest.import_change);
")
if [[ "$counts" != 1,5,0,* ]]; then
  echo "unexpected staging counts: $counts" >&2
  exit 1
fi

echo "local data ingest prototype passed: batch,row,issue,change=$counts"
echo "legacy migration preview passed: 33 rows"
echo "local Runtime discovery previews passed: OpenClaw and Hermes"
