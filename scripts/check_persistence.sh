#!/usr/bin/env bash
set -euo pipefail

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CLICKHOUSE_HTTP_URL="${CLICKHOUSE_HTTP_URL:-http://127.0.0.1:${CLICKHOUSE_HTTP_PORT:-8123}}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-otel}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-otel}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-change-me}"
OTEL_HEALTH_URL="${OTEL_HEALTH_URL:-http://127.0.0.1:${OTEL_HEALTH_PORT:-13133}}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:${PROMETHEUS_PORT:-9090}}"
THANOS_QUERY_URL="${THANOS_QUERY_URL:-http://127.0.0.1:${THANOS_QUERY_PORT:-10902}}"

query_clickhouse() {
  curl -fsS \
    --user "${CLICKHOUSE_USER}:${CLICKHOUSE_PASSWORD}" \
    --data-binary "$1" \
    "${CLICKHOUSE_HTTP_URL}/"
}

curl -fsS "${CLICKHOUSE_HTTP_URL}/ping" >/dev/null
curl -fsS "${OTEL_HEALTH_URL}/" >/dev/null
curl -fsS "${PROMETHEUS_URL}/-/ready" >/dev/null
curl -fsS "${THANOS_QUERY_URL}/-/ready" >/dev/null

query_clickhouse "
SELECT
  name AS table,
  formatReadableQuantity(total_rows) AS rows,
  formatReadableSize(total_bytes) AS disk
FROM system.tables
WHERE database = '${CLICKHOUSE_DATABASE}' AND startsWith(name, 'otel_')
ORDER BY name
FORMAT PrettyCompact
"

echo "persistence backends are ready"
echo "note: table row growth does not prove that a specific Gateway is scrapeable or that its counters increased"
echo "validate each Gateway with check_openclaw_telemetry.py and Prometheus target health"
