#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ENV_FILE:-${root_dir}/.env}"
secrets_dir="${PROMETHEUS_SECRETS_DIR:-${root_dir}/prometheus/secrets/gateways}"

if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

prometheus_gid="${PROMETHEUS_SECRET_GID:-65534}"
if [[ ! "$prometheus_gid" =~ ^[0-9]+$ ]]; then
  echo "PROMETHEUS_SECRET_GID must be numeric: $prometheus_gid" >&2
  exit 2
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "run with sudo so the host can assign group $prometheus_gid" >&2
  exit 1
fi

owner_uid="${PROMETHEUS_SECRET_OWNER_UID:-${SUDO_UID:-0}}"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/docker-compose.yml")
if [[ -f "$env_file" ]]; then
  compose+=(--env-file "$env_file")
fi
container_gid="$("${compose[@]}" run --rm --no-deps --entrypoint sh prometheus -c 'id -g')"
if [[ "$container_gid" != "$prometheus_gid" ]]; then
  echo "Prometheus image runs with gid $container_gid, but PROMETHEUS_SECRET_GID=$prometheus_gid" >&2
  exit 1
fi

mkdir -p "$secrets_dir"
chown "$owner_uid:$prometheus_gid" "$secrets_dir"
chmod 2750 "$secrets_dir"

while IFS= read -r -d '' token_file; do
  chown "$owner_uid:$prometheus_gid" "$token_file"
  chmod 0640 "$token_file"
done < <(find "$secrets_dir" -maxdepth 1 -type f -name '*.token' -print0)

echo "prepared $secrets_dir for owner uid $owner_uid and Prometheus gid $prometheus_gid"
echo "directory mode: 2750; token mode: 0640"
