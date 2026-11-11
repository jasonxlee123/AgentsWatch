#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "prepare_tls.sh must run as root" >&2
  exit 1
fi

database_dns_name="${AGENTSWATCH_DATABASE_DNS_NAME:-agentswatch-db}"
postgres_data_root="${AGENTSWATCH_POSTGRES_DATA_ROOT:-/data/agentswatch/management/postgres}"
postgres_root="${AGENTSWATCH_POSTGRES_CONFIG_ROOT:-/etc/agentswatch/postgres}"
tls_dir="${AGENTSWATCH_POSTGRES_TLS_DIR:-${postgres_root}/tls}"
private_dir="${AGENTSWATCH_POSTGRES_PRIVATE_DIR:-${postgres_root}/private}"
client_ca_dir="${AGENTSWATCH_DATABASE_CA_DIR:-/etc/agentswatch/database}"

postgres_gid="${AGENTSWATCH_POSTGRES_GID:-}"
if [[ -z "${postgres_gid}" ]]; then
  if [[ ! -d "${postgres_data_root}" ]]; then
    echo "set AGENTSWATCH_POSTGRES_GID when PostgreSQL data does not exist yet" >&2
    exit 1
  fi
  read -r postgres_data_uid postgres_data_gid < <(stat -c '%u %g' "${postgres_data_root}")
  if [[ "${postgres_data_gid}" == "0" && "${postgres_data_uid}" != "0" ]]; then
    # Container PGDATA may be owned by postgres but retain root as its host-side group.
    # Official images normally use the same numeric UID and GID for the postgres user.
    postgres_gid="${postgres_data_uid}"
  else
    postgres_gid="${postgres_data_gid}"
  fi
fi
if [[ ! "${postgres_gid}" =~ ^[0-9]+$ ]]; then
  echo "AGENTSWATCH_POSTGRES_GID must be numeric" >&2
  exit 1
fi

ca_key="${private_dir}/ca.key"
ca_cert="${tls_dir}/ca.crt"
server_key="${tls_dir}/server.key"
server_cert="${tls_dir}/server.crt"
client_ca="${client_ca_dir}/ca.crt"

public_key_fingerprint() {
  openssl pkey -in "$1" -pubout -outform DER 2>/dev/null \
    | openssl dgst -sha256
}

certificate_key_fingerprint() {
  openssl x509 -in "$1" -pubkey -noout 2>/dev/null \
    | openssl pkey -pubin -outform DER 2>/dev/null \
    | openssl dgst -sha256
}

file_identity() {
  stat -c '%u:%g:%a' "$1"
}

if [[ -e "${ca_key}" || -e "${ca_cert}" || -e "${server_key}" || -e "${server_cert}" || -e "${client_ca}" ]]; then
  if [[ -s "${ca_key}" && -s "${ca_cert}" && -s "${server_key}" && -s "${server_cert}" && -s "${client_ca}" ]] \
    && [[ "$(file_identity "${ca_key}")" == "0:0:600" ]] \
    && [[ "$(file_identity "${ca_cert}")" == "0:0:444" ]] \
    && [[ "$(file_identity "${server_key}")" == "0:${postgres_gid}:640" ]] \
    && [[ "$(file_identity "${server_cert}")" == "0:0:444" ]] \
    && [[ "$(file_identity "${client_ca}")" == "0:0:444" ]] \
    && [[ "$(file_identity "${private_dir}")" == "0:0:700" ]] \
    && [[ "$(file_identity "${tls_dir}")" == "0:0:755" ]] \
    && [[ "$(file_identity "${client_ca_dir}")" == "0:0:755" ]] \
    && [[ "$(public_key_fingerprint "${ca_key}")" == "$(certificate_key_fingerprint "${ca_cert}")" ]] \
    && [[ "$(public_key_fingerprint "${server_key}")" == "$(certificate_key_fingerprint "${server_cert}")" ]] \
    && openssl x509 -in "${ca_cert}" -noout -checkend 2592000 >/dev/null \
    && openssl x509 -in "${server_cert}" -noout -checkend 2592000 >/dev/null \
    && openssl x509 -in "${server_cert}" -noout -checkhost "${database_dns_name}" >/dev/null \
    && openssl verify -CAfile "${ca_cert}" "${server_cert}" >/dev/null \
    && cmp -s "${ca_cert}" "${client_ca}"; then
    echo "existing AgentsWatch database TLS material is valid"
    exit 0
  fi
  echo "refusing to overwrite incomplete, mismatched, or expiring database TLS material" >&2
  exit 1
fi

temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
umask 077

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "${temporary}/ca.key"
openssl req -x509 -new -sha256 -days 3650 \
  -key "${temporary}/ca.key" \
  -subj "/CN=AgentsWatch Database Internal CA" \
  -out "${temporary}/ca.crt"

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "${temporary}/server.key"
openssl req -new -sha256 \
  -key "${temporary}/server.key" \
  -subj "/CN=${database_dns_name}" \
  -out "${temporary}/server.csr"
printf '%s\n' \
  'basicConstraints=critical,CA:FALSE' \
  'keyUsage=critical,digitalSignature,keyEncipherment' \
  'extendedKeyUsage=serverAuth' \
  "subjectAltName=DNS:${database_dns_name}" \
  > "${temporary}/server.ext"
openssl x509 -req -sha256 -days 825 \
  -in "${temporary}/server.csr" \
  -CA "${temporary}/ca.crt" \
  -CAkey "${temporary}/ca.key" \
  -CAcreateserial \
  -extfile "${temporary}/server.ext" \
  -out "${temporary}/server.crt"

openssl x509 -in "${temporary}/server.crt" -noout -checkhost "${database_dns_name}"
openssl verify -CAfile "${temporary}/ca.crt" "${temporary}/server.crt"

install -d -o root -g root -m 0700 "${private_dir}"
install -d -o root -g root -m 0755 "${tls_dir}" "${client_ca_dir}"
install -o root -g root -m 0600 "${temporary}/ca.key" "${ca_key}"
install -o root -g root -m 0444 "${temporary}/ca.crt" "${ca_cert}"
install -o root -g "${postgres_gid}" -m 0640 "${temporary}/server.key" "${server_key}"
install -o root -g root -m 0444 "${temporary}/server.crt" "${server_cert}"
install -o root -g root -m 0444 "${temporary}/ca.crt" "${client_ca}"

echo "created AgentsWatch database TLS material for ${database_dns_name}"
