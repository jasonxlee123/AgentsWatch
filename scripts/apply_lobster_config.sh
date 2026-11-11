#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/apply_lobster_config.sh <gateway_id> [options]

Options:
  --ssh user@host          Apply through SSH. Requires --known-hosts.
  --known-hosts file       Explicit pinned OpenSSH known_hosts file.
  --identity-file file     Optional private key; otherwise use SSH agent/default keys.
  --container name         Run OpenClaw commands inside this container.
  --config-path path       Absolute target config path. Defaults on the target to
                           $OPENCLAW_CONFIG_PATH or $HOME/.openclaw/openclaw.json.
  --patch-dir dir          Patch directory (default: cluster/openclaw-monitoring-patches).
  --connect-timeout sec    SSH connection timeout (default: 10).
  --health-timeout sec     Total post-restart health wait (default: 60).
  --health-interval sec    Delay between health probes (default: 2).
  --probe-timeout sec      Maximum time for one health probe (default: 10).
  --dry-run                Validate plugins and patch without backup or mutation.

Examples:
  scripts/apply_lobster_config.sh gateway-example-001 \
    --ssh openclaw@gateway-001.example.invalid --known-hosts /etc/agentswatch/ssh_known_hosts
  scripts/apply_lobster_config.sh gateway-example-002 \
    --ssh openclaw@gateway-002.example.invalid --known-hosts /etc/agentswatch/ssh_known_hosts \
    --container openclaw

Actual apply validates first, creates a timestamped mode-0600 config backup,
enables the plugins, applies the patch, restarts, and waits for Gateway health.
Any failure after backup restores that backup and restarts the previous config.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

gateway_id="$1"
shift

ssh_target=""
known_hosts_file=""
identity_file=""
container_name=""
config_path=""
patch_dir="cluster/openclaw-monitoring-patches"
connect_timeout="10"
health_timeout="60"
health_interval="2"
probe_timeout="10"
dry_run="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh)
      ssh_target="${2:-}"
      shift 2
      ;;
    --known-hosts)
      known_hosts_file="${2:-}"
      shift 2
      ;;
    --identity-file)
      identity_file="${2:-}"
      shift 2
      ;;
    --container)
      container_name="${2:-}"
      shift 2
      ;;
    --config-path)
      config_path="${2:-}"
      shift 2
      ;;
    --patch-dir)
      patch_dir="${2:-}"
      shift 2
      ;;
    --connect-timeout)
      connect_timeout="${2:-}"
      shift 2
      ;;
    --health-timeout)
      health_timeout="${2:-}"
      shift 2
      ;;
    --health-interval)
      health_interval="${2:-}"
      shift 2
      ;;
    --probe-timeout)
      probe_timeout="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

if [[ ! "$gateway_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "gateway_id contains unsupported characters: $gateway_id" >&2
  exit 2
fi

for timeout_value in "$connect_timeout" "$health_timeout" "$health_interval" "$probe_timeout"; do
  if ! is_positive_integer "$timeout_value"; then
    echo "timeouts and intervals must be positive integers" >&2
    exit 2
  fi
done

if [[ -n "$config_path" && "$config_path" != /* ]]; then
  echo "--config-path must be an absolute path on the target" >&2
  exit 2
fi

if [[ -n "$ssh_target" ]]; then
  if [[ -z "$known_hosts_file" ]]; then
    echo "--known-hosts is required with --ssh" >&2
    exit 2
  fi
  if [[ ! -f "$known_hosts_file" || ! -r "$known_hosts_file" || ! -s "$known_hosts_file" ]]; then
    echo "known_hosts file must be a non-empty readable regular file: $known_hosts_file" >&2
    exit 2
  fi
  if [[ -n "$identity_file" && (! -f "$identity_file" || ! -r "$identity_file") ]]; then
    echo "identity file must be a readable regular file: $identity_file" >&2
    exit 2
  fi
elif [[ -n "$known_hosts_file" || -n "$identity_file" ]]; then
  echo "--known-hosts and --identity-file are only valid with --ssh" >&2
  exit 2
fi

patch_file="${patch_dir}/${gateway_id}.json"
if [[ ! -f "$patch_file" ]]; then
  echo "patch file not found: $patch_file" >&2
  exit 1
fi

ssh_options=(
  -o "ConnectTimeout=${connect_timeout}"
  -o "StrictHostKeyChecking=yes"
  -o "UserKnownHostsFile=${known_hosts_file}"
  -o "GlobalKnownHostsFile=/dev/null"
  -o "UpdateHostKeys=no"
  -o "BatchMode=yes"
  -o "PreferredAuthentications=publickey"
  -o "PubkeyAuthentication=yes"
  -o "PasswordAuthentication=no"
  -o "KbdInteractiveAuthentication=no"
  -o "ChallengeResponseAuthentication=no"
  -o "NumberOfPasswordPrompts=0"
)
if [[ -n "$identity_file" ]]; then
  ssh_options+=(
    -o "IdentitiesOnly=yes"
    -i "$identity_file"
  )
fi

run_ssh() {
  ssh "${ssh_options[@]}" "$@"
}

remote_openclaw_script='set -eu
openclaw_bin="$(command -v openclaw 2>/dev/null || true)"
if [ -n "$openclaw_bin" ] && [ ! -x "$openclaw_bin" ]; then
  openclaw_bin=""
fi
if [ -z "$openclaw_bin" ]; then
  for candidate in \
    "$HOME"/.openclaw/bin/openclaw \
    "$HOME"/.local/bin/openclaw \
    "$HOME"/.npm-global/bin/openclaw \
    "$HOME"/.local/share/pnpm/openclaw \
    "$HOME"/.bun/bin/openclaw \
    "$HOME"/.volta/bin/openclaw \
    "$HOME"/.asdf/shims/openclaw \
    "$HOME"/.local/share/mise/shims/openclaw \
    "$HOME"/.nvm/versions/node/*/bin/openclaw; do
    if [ -x "$candidate" ]; then
      openclaw_bin="$candidate"
      break
    fi
  done
fi
if [ -z "$openclaw_bin" ] && command -v bash >/dev/null 2>&1; then
  bashrc_openclaw_bin="$(bash -ic "type -P openclaw" 2>/dev/null | tail -n 1 || true)"
  if [ -x "$bashrc_openclaw_bin" ]; then
    openclaw_bin="$bashrc_openclaw_bin"
  fi
fi
if [ -z "$openclaw_bin" ]; then
  for candidate in /usr/local/bin/openclaw /usr/bin/openclaw /opt/homebrew/bin/openclaw; do
    if [ -x "$candidate" ]; then
      openclaw_bin="$candidate"
      break
    fi
  done
fi
if [ -z "$openclaw_bin" ]; then
  echo "OpenClaw executable not found for user $(id -un); checked PATH and common user install directories" >&2
  exit 127
fi
openclaw_dir="$(dirname "$openclaw_bin")"
node_bin=""
for candidate in \
  "$openclaw_dir"/node \
  "$HOME"/.volta/bin/node \
  "$HOME"/.asdf/shims/node \
  "$HOME"/.local/share/mise/shims/node \
  "$HOME"/.nvm/versions/node/*/bin/node; do
  if [ -x "$candidate" ]; then
    node_bin="$candidate"
    break
  fi
done
if [ -z "$node_bin" ] && command -v bash >/dev/null 2>&1; then
  bashrc_node_bin="$(bash -ic "type -P node" 2>/dev/null | tail -n 1 || true)"
  if [ -x "$bashrc_node_bin" ]; then
    node_bin="$bashrc_node_bin"
  fi
fi
if [ -z "$node_bin" ]; then
  node_bin="$(command -v node 2>/dev/null || true)"
  if [ -n "$node_bin" ] && [ ! -x "$node_bin" ]; then
    node_bin=""
  fi
fi
if [ -z "$node_bin" ]; then
  for candidate in /usr/local/bin/node /usr/bin/node /opt/homebrew/bin/node; do
    if [ -x "$candidate" ]; then
      node_bin="$candidate"
      break
    fi
  done
fi
if [ -n "$node_bin" ]; then
  export PATH="$openclaw_dir:$(dirname "$node_bin"):$PATH"
else
  export PATH="$openclaw_dir:$PATH"
fi
exec "$openclaw_bin" "$@"'

shell_quote() {
  local value="${1//\'/\'\\\'\'}"
  printf "'%s'" "$value"
}

run_remote_openclaw() {
  local command arg
  command="sh -c $(shell_quote "$remote_openclaw_script") sh"
  for arg in "$@"; do
    command+=" $(shell_quote "$arg")"
  done
  run_ssh "$ssh_target" "$command"
}

target_openclaw() {
  local command arg
  if [[ -z "$ssh_target" ]]; then
    if [[ -n "$container_name" ]]; then
      docker exec -i "$container_name" openclaw "$@"
    else
      openclaw "$@"
    fi
  elif [[ -n "$container_name" ]]; then
    command="docker exec -i $(shell_quote "$container_name") openclaw"
    for arg in "$@"; do
      command+=" $(shell_quote "$arg")"
    done
    run_ssh "$ssh_target" "$command"
  else
    run_remote_openclaw "$@"
  fi
}

target_shell() {
  local target_script="$1"
  shift
  local command arg
  if [[ -z "$ssh_target" ]]; then
    if [[ -n "$container_name" ]]; then
      docker exec -i "$container_name" sh -c "$target_script" sh "$@"
    else
      sh -c "$target_script" sh "$@"
    fi
  else
    if [[ -n "$container_name" ]]; then
      command="docker exec -i $(shell_quote "$container_name") sh -c $(shell_quote "$target_script") sh"
    else
      command="sh -c $(shell_quote "$target_script") sh"
    fi
    for arg in "$@"; do
      command+=" $(shell_quote "$arg")"
    done
    run_ssh "$ssh_target" "$command"
  fi
}

target_restart() {
  if [[ -z "$container_name" ]]; then
    target_openclaw gateway restart
  elif [[ -z "$ssh_target" ]]; then
    docker restart "$container_name"
  else
    run_ssh "$ssh_target" "docker restart $(shell_quote "$container_name")"
  fi
}

backup_target_script='set -eu
requested_path=$1
if [ -n "$requested_path" ]; then
  config_path=$requested_path
else
  config_path=${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}
fi
case "$config_path" in
  /*) ;;
  *) echo "resolved OpenClaw config path is not absolute: $config_path" >&2; exit 2 ;;
esac
if [ ! -f "$config_path" ]; then
  echo "OpenClaw config file not found: $config_path" >&2
  exit 1
fi
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_path="${config_path}.agentswatch-backup.${timestamp}"
suffix=0
while [ -e "$backup_path" ] || [ -L "$backup_path" ]; do
  suffix=$((suffix + 1))
  backup_path="${config_path}.agentswatch-backup.${timestamp}.${suffix}"
done
temporary_path="${backup_path}.tmp.$$"
trap '\''rm -f "$temporary_path"'\'' 0 1 2 15
umask 077
cp "$config_path" "$temporary_path"
chmod 600 "$temporary_path"
mv "$temporary_path" "$backup_path"
trap - 0 1 2 15
printf "%s\n" "$backup_path"'

restore_target_script='set -eu
requested_path=$1
backup_path=$2
if [ -n "$requested_path" ]; then
  config_path=$requested_path
else
  config_path=${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}
fi
case "$config_path" in
  /*) ;;
  *) echo "resolved OpenClaw config path is not absolute: $config_path" >&2; exit 2 ;;
esac
if [ ! -f "$backup_path" ]; then
  echo "OpenClaw backup file not found: $backup_path" >&2
  exit 1
fi
temporary_path="${config_path}.agentswatch-restore.tmp.$$"
trap '\''rm -f "$temporary_path"'\'' 0 1 2 15
umask 077
cp "$backup_path" "$temporary_path"
chmod 600 "$temporary_path"
mv "$temporary_path" "$config_path"
trap - 0 1 2 15'

plugins=(diagnostics-otel diagnostics-prometheus)

preflight() {
  local plugin
  for plugin in "${plugins[@]}"; do
    target_openclaw plugins inspect "$plugin" --json >/dev/null
  done
  target_openclaw config patch --stdin --dry-run < "$patch_file"
}

run_with_timeout() {
  local timeout_seconds="$1"
  shift
  "$@" &
  local command_pid=$!
  (
    sleep "$timeout_seconds"
    kill -TERM "$command_pid" 2>/dev/null || exit 0
    sleep 1
    kill -KILL "$command_pid" 2>/dev/null || true
  ) &
  local watchdog_pid=$!
  local command_status
  set +e
  wait "$command_pid"
  command_status=$?
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  set -e
  return "$command_status"
}

health_probe() {
  target_openclaw gateway status >/dev/null 2>&1
}

wait_for_health() {
  local deadline=$((SECONDS + health_timeout))
  local remaining attempt_timeout sleep_seconds
  while true; do
    remaining=$((deadline - SECONDS))
    if (( remaining <= 0 )); then
      break
    fi
    attempt_timeout="$probe_timeout"
    if (( attempt_timeout > remaining )); then
      attempt_timeout="$remaining"
    fi
    if run_with_timeout "$attempt_timeout" health_probe; then
      echo "Gateway health check passed"
      return 0
    fi
    remaining=$((deadline - SECONDS))
    if (( remaining <= 0 )); then
      break
    fi
    sleep_seconds="$health_interval"
    if (( sleep_seconds > remaining )); then
      sleep_seconds="$remaining"
    fi
    sleep "$sleep_seconds"
  done
  echo "Gateway health check did not pass within ${health_timeout}s" >&2
  return 1
}

failure_stage=""
perform_apply() {
  local plugin
  for plugin in "${plugins[@]}"; do
    if ! target_openclaw plugins enable "$plugin"; then
      failure_stage="enable plugin ${plugin}"
      return 1
    fi
  done
  if ! target_openclaw config patch --stdin < "$patch_file"; then
    failure_stage="apply config patch"
    return 1
  fi
  if ! target_restart; then
    failure_stage="restart Gateway"
    return 1
  fi
  if ! wait_for_health; then
    failure_stage="post-restart health check"
    return 1
  fi
}

rollback_after_failure() {
  local original_status="$1"
  local restore_status=0
  local restart_status=0
  echo "apply failed during ${failure_stage}; restoring backup: ${backup_path}" >&2
  if target_shell "$restore_target_script" "$config_path" "$backup_path"; then
    echo "restored config backup: ${backup_path}" >&2
  else
    restore_status=$?
    echo "failed to restore config backup: ${backup_path}" >&2
  fi
  if target_restart; then
    echo "restarted Gateway after rollback" >&2
  else
    restart_status=$?
    echo "failed to restart Gateway after rollback" >&2
  fi
  echo "backup retained at: ${backup_path}" >&2
  if (( restore_status != 0 || restart_status != 0 )); then
    return 1
  fi
  if (( original_status == 0 )); then
    return 1
  fi
  return "$original_status"
}

preflight
if [[ "$dry_run" == "true" ]]; then
  echo "dry-run passed for Gateway ${gateway_id}; no backup or configuration change was made"
  exit 0
fi

backup_output="$(target_shell "$backup_target_script" "$config_path")"
backup_path="$(printf '%s\n' "$backup_output" | tail -n 1)"
if [[ -z "$backup_path" || "$backup_path" != /* ]]; then
  echo "target did not return an absolute backup path" >&2
  exit 1
fi
echo "created config backup: ${backup_path}"

apply_status=0
perform_apply || apply_status=$?
if (( apply_status != 0 )); then
  rollback_status=0
  rollback_after_failure "$apply_status" || rollback_status=$?
  exit "$rollback_status"
fi

echo "Gateway ${gateway_id} configuration applied successfully"
echo "backup retained at: ${backup_path}"
