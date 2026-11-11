#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ENV_FILE:-${root_dir}/.env}"
force=false
if [[ "${1:-}" == "--force" ]]; then
  force=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--force]" >&2
  exit 2
fi

if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

public_host="${GRAFANA_PUBLIC_HOST:-localhost}"
valid_days="${NGINX_SELF_SIGNED_DAYS:-825}"
cert_dir="${NGINX_CERT_DIR:-${root_dir}/nginx/certs}"
cert_file="${cert_dir}/tls.crt"
key_file="${cert_dir}/tls.key"

if [[ "$force" != true && -s "$cert_file" && -s "$key_file" ]]; then
  openssl x509 -in "$cert_file" -noout -checkend 0 >/dev/null
  echo "existing TLS certificate is valid: $cert_file"
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate a self-signed certificate" >&2
  exit 1
fi
if [[ ! "$valid_days" =~ ^[0-9]+$ || "$valid_days" -lt 1 ]]; then
  echo "NGINX_SELF_SIGNED_DAYS must be a positive integer" >&2
  exit 2
fi

if [[ "$public_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  subject_alt_name="IP:${public_host}"
else
  subject_alt_name="DNS:${public_host}"
fi

mkdir -p "$cert_dir"
temporary_dir="$(mktemp -d "${cert_dir}/.tls.XXXXXX")"
cleanup() {
  rm -rf "$temporary_dir"
}
trap cleanup EXIT
umask 077

openssl req -x509 -nodes -newkey rsa:3072 -sha256 \
  -days "$valid_days" \
  -keyout "${temporary_dir}/tls.key" \
  -out "${temporary_dir}/tls.crt" \
  -subj "/CN=${public_host}" \
  -addext "subjectAltName=${subject_alt_name}" >/dev/null 2>&1

openssl x509 -in "${temporary_dir}/tls.crt" -noout -checkend 0 >/dev/null
chmod 0600 "${temporary_dir}/tls.key"
chmod 0644 "${temporary_dir}/tls.crt"
mv "${temporary_dir}/tls.key" "$key_file"
mv "${temporary_dir}/tls.crt" "$cert_file"

echo "generated self-signed TLS certificate for ${public_host}"
echo "replace nginx/certs/tls.crt and tls.key with an organization-issued certificate when available"
