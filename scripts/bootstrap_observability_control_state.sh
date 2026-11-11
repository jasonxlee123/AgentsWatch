#!/bin/sh
set -eu

root=${1:-/data/agentswatch/observability-control}
writer_uid=${2:-10001}
writer_gid=${3:-10001}
revisions="$root/revisions"
staging="$root/staging"
current="$root/current"
bootstrap="$revisions/bootstrap"

case "$writer_uid" in ''|*[!0-9]*) echo "writer UID must be numeric" >&2; exit 2 ;; esac
case "$writer_gid" in ''|*[!0-9]*) echo "writer GID must be numeric" >&2; exit 2 ;; esac

mkdir -p "$root" "$revisions" "$staging"
chown "$writer_uid:$writer_gid" "$root" "$revisions" "$staging"
chmod 2750 "$root" "$revisions" "$staging" 2>/dev/null \
  || chmod 0750 "$root" "$revisions" "$staging"

if [ ! -e "$current" ] && [ ! -L "$current" ]; then
  mkdir -p "$bootstrap/openclaw-monitoring-patches"
  printf '%s\n' 'scrape_configs: []' >"$bootstrap/openclaw-gateways.yml"
  printf '%s\n' 'groups:' '  - name: agentswatch-gateway-inventory' '    rules: []' \
    >"$bootstrap/agentswatch-gateway-info.rules.yml"
  printf '%s\n' \
    '{"schema_version":1,"source":"bootstrap","snapshot_hash":"bootstrap","rows":[]}' \
    >"$bootstrap/snapshot.json"
  printf '%s\n' '{"defaults":{},"gateways":[]}' >"$bootstrap/inventory.json"
  printf '%s\n' \
    '{"schema_version":1,"revision_id":"bootstrap","source":"bootstrap","snapshot_hash":"bootstrap","canonical_hash":"bootstrap","created_at":"1970-01-01T00:00:00+00:00","summary":{"total":0,"deployable":0,"blocked":0,"orphan":0,"native_scrape_enabled":0},"artifacts":{}}' \
    >"$bootstrap/manifest.json"
  ln -s revisions/bootstrap "$current"
fi

if [ -d "$bootstrap" ]; then
  chown -R "$writer_uid:$writer_gid" "$bootstrap"
  chmod 2750 "$bootstrap" "$bootstrap/openclaw-monitoring-patches" 2>/dev/null \
    || chmod 0750 "$bootstrap" "$bootstrap/openclaw-monitoring-patches"
  chmod 0640 "$bootstrap"/*.json "$bootstrap"/*.yml
fi

for revision in "$revisions"/*; do
  [ -d "$revision" ] && [ ! -L "$revision" ] || continue
  chown "$writer_uid:$writer_gid" "$revision"
  chmod 2750 "$revision" 2>/dev/null || chmod 0750 "$revision"
  for artifact in "$revision"/*; do
    if [ -d "$artifact" ] && [ ! -L "$artifact" ]; then
      chown "$writer_uid:$writer_gid" "$artifact"
      chmod 2750 "$artifact" 2>/dev/null || chmod 0750 "$artifact"
      for nested_artifact in "$artifact"/*; do
        [ -f "$nested_artifact" ] && [ ! -L "$nested_artifact" ] || continue
        chown "$writer_uid:$writer_gid" "$nested_artifact"
        chmod 0640 "$nested_artifact"
      done
    elif [ -f "$artifact" ] && [ ! -L "$artifact" ]; then
      chown "$writer_uid:$writer_gid" "$artifact"
      chmod 0640 "$artifact"
    fi
  done
done
