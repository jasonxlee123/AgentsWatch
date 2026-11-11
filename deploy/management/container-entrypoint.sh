#!/bin/sh
set -eu

umask 027

requested_data_root="${AGENTSWATCH_MANAGEMENT_DATA_ROOT:-/data/agentswatch/management}"
nginx_gid="${AGENTSWATCH_NGINX_GID:-101}"

reject_parent_segments() {
  label="$1"
  value="$2"
  case "${value}" in
    */../*|*/..)
      echo "${label} must not contain parent-directory components: ${value}" >&2
      exit 64
      ;;
  esac
}

case "${requested_data_root}" in
  /*) ;;
  *)
    echo "AGENTSWATCH_MANAGEMENT_DATA_ROOT must be an absolute path" >&2
    exit 64
    ;;
esac
reject_parent_segments "AGENTSWATCH_MANAGEMENT_DATA_ROOT" "${requested_data_root}"

if [ ! -d "${requested_data_root}" ]; then
  echo "AGENTSWATCH_MANAGEMENT_DATA_ROOT must be a pre-created directory" >&2
  exit 73
fi
data_root="$(realpath -e -- "${requested_data_root}")"

resolve_data_child() {
  label="$1"
  value="$2"
  case "${value}" in
    /*) ;;
    *)
      echo "${label} must be an absolute path" >&2
      exit 64
      ;;
  esac
  reject_parent_segments "${label}" "${value}"
  physical="$(realpath -m -- "${value}")"
  case "${physical}" in
    "${data_root}"/*) printf '%s\n' "${physical}" ;;
    *)
      echo "${label} resolves outside ${data_root}: ${value}" >&2
      exit 64
      ;;
  esac
}

automation_root="$(resolve_data_child \
  AGENTSWATCH_AUTOMATION_DATA_ROOT \
  "${AGENTSWATCH_AUTOMATION_DATA_ROOT:-${data_root}/automation}")"
home_root="$(resolve_data_child HOME "${HOME:-${data_root}/home}")"
run_root="$(resolve_data_child AGENTSWATCH_API_RUN_DIR "${data_root}/run")"
credentials_root="$(resolve_data_child AGENTSWATCH_CREDENTIAL_DIR "${data_root}/credentials")"

if [ ! -d "${run_root}" ]; then
  echo "${run_root} must be pre-created with owner UID 10001, group GID ${nginx_gid} and mode 2750" >&2
  exit 73
fi

actual_run_gid="$(stat -c %g "${run_root}")"
if [ "${actual_run_gid}" != "${nginx_gid}" ]; then
  echo "${run_root} has GID ${actual_run_gid}; expected Nginx GID ${nginx_gid}" >&2
  exit 73
fi

actual_run_uid="$(stat -c %u "${run_root}")"
if [ "${actual_run_uid}" != "10001" ]; then
  echo "${run_root} has UID ${actual_run_uid}; expected AgentsWatch UID 10001" >&2
  exit 73
fi

actual_run_mode="$(stat -c %a "${run_root}")"
if [ "${actual_run_mode}" != "2750" ]; then
  echo "${run_root} has mode ${actual_run_mode}; expected setgid mode 2750" >&2
  exit 73
fi

if [ ! -w "${run_root}" ]; then
  echo "${run_root} is not writable by the AgentsWatch process" >&2
  exit 73
fi

mkdir -p \
  "${automation_root}/managed" \
  "${automation_root}/artifacts" \
  "${automation_root}/.runner" \
  "${credentials_root}" \
  "${home_root}/.ansible" \
  "${home_root}/.ssh"

chmod 0700 \
  "${automation_root}" \
  "${automation_root}/managed" \
  "${automation_root}/artifacts" \
  "${automation_root}/.runner" \
  "${credentials_root}" \
  "${home_root}" \
  "${home_root}/.ansible" \
  "${home_root}/.ssh"

managed_ssh_key="${AGENTSWATCH_DEFAULT_SSH_PRIVATE_KEY_FILE:-${credentials_root}/agentswatch-managed}"
case "${managed_ssh_key}" in
  "${credentials_root}"/*) ;;
  *)
    echo "AGENTSWATCH_DEFAULT_SSH_PRIVATE_KEY_FILE must be inside ${credentials_root}" >&2
    exit 64
    ;;
esac
if [ ! -f "${managed_ssh_key}" ]; then
  ssh-keygen -q -t ed25519 -N '' -C 'agentswatch-managed' -f "${managed_ssh_key}"
fi
if [ ! -f "${managed_ssh_key}.pub" ]; then
  ssh-keygen -y -f "${managed_ssh_key}" > "${managed_ssh_key}.pub"
fi
chmod 0600 "${managed_ssh_key}"
chmod 0644 "${managed_ssh_key}.pub"

rm -f "${run_root}/api.sock"

exec "$@"
