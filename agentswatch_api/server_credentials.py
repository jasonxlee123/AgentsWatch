import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


SSH_HOST = re.compile(r"^[A-Za-z0-9_.:\-]+$")
SSH_KEY_TYPES = {
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "ssh-rsa",
}


class ServerCredentialCipher:
    """Encrypt persistent server login credentials independently from WebUI secrets."""

    def __init__(self, key: str | None = None):
        raw = (key or os.environ.get("AGENTSWATCH_SSH_CREDENTIAL_KEY") or "").strip()
        if not raw:
            raise RuntimeError(
                "AGENTSWATCH_SSH_CREDENTIAL_KEY is required to save or use SSH passwords"
            )
        try:
            decoded = base64.urlsafe_b64decode(raw.encode())
            if len(decoded) != 32:
                raise ValueError
            self.fernet = Fernet(raw.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "AGENTSWATCH_SSH_CREDENTIAL_KEY must be a URL-safe base64 encoded 32-byte key"
            ) from exc

    def encrypt(self, password: str) -> str:
        if not password:
            raise ValueError("SSH password cannot be empty")
        payload = json.dumps({"password": password}, separators=(",", ":")).encode()
        return self.fernet.encrypt(payload).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            payload = json.loads(self.fernet.decrypt(ciphertext.encode()).decode())
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "saved SSH password cannot be decrypted with the configured key"
            ) from exc
        password = payload.get("password") if isinstance(payload, dict) else None
        if not isinstance(password, str) or not password:
            raise RuntimeError("saved SSH password payload is invalid")
        return password


def _validate_host(host: str) -> str:
    value = str(host or "").strip()
    if not value or len(value) > 500 or not SSH_HOST.fullmatch(value) or value.startswith("-"):
        raise ValueError("SSH host must be an IP address or DNS name")
    return value


def validate_ssh_connection_host(
    host: str, *, allow_loopback: bool | None = None,
) -> str:
    """Validate an SSH endpoint and keep loopback access development-only."""

    value = _validate_host(host)
    normalized = value.rstrip(".").lower()
    parsed = None
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError:
        # OpenSSH/getaddrinfo accept several legacy numeric IPv4 spellings
        # (for example 127.1 or 2130706433) that ipaddress deliberately
        # rejects.  Do not let those strings bypass the literal-IP policy by
        # treating them as DNS names.
        try:
            socket.inet_aton(normalized)
        except OSError:
            pass
        else:
            raise ValueError("SSH host uses an unsupported numeric IP notation")
    loopback = (
        normalized == "localhost"
        or normalized.startswith("localhost.")
        or normalized.endswith(".localhost")
    )
    if parsed is not None:
        loopback = loopback or parsed.is_loopback
        if parsed.is_unspecified or parsed.is_multicast:
            raise ValueError("SSH host must not be an unspecified or multicast address")
    if allow_loopback is None:
        allow_loopback = os.environ.get("AGENTSWATCH_ALLOW_LOCAL_CONNECTION") == "true"
    if loopback and not allow_loopback:
        raise ValueError(
            "SSH loopback hosts are disabled; "
            "AGENTSWATCH_ALLOW_LOCAL_CONNECTION=true is only for local development"
        )
    return value


def _fingerprint(key_data: str) -> str:
    try:
        decoded = base64.b64decode(key_data.encode(), validate=True)
    except ValueError as exc:
        raise ValueError("SSH host key is not valid base64") from exc
    digest = base64.b64encode(hashlib.sha256(decoded).digest()).decode().rstrip("=")
    return f"SHA256:{digest}"


def validate_ssh_host_keys(host: str, port: int, keys: list[str]) -> tuple[list[str], list[str]]:
    host = _validate_host(host)
    expected_hosts = {host, f"[{host}]:{port}"}
    normalized: list[str] = []
    fingerprints: list[str] = []
    for line in keys:
        parts = str(line or "").strip().split()
        if len(parts) != 3 or parts[0] not in expected_hosts or parts[1] not in SSH_KEY_TYPES:
            raise ValueError("SSH host key does not match the configured host and port")
        fingerprint = _fingerprint(parts[2])
        canonical = " ".join(parts)
        if canonical not in normalized:
            normalized.append(canonical)
            fingerprints.append(f"{parts[1]} {fingerprint}")
    if not normalized:
        raise ValueError("at least one confirmed SSH host key is required")
    return normalized, fingerprints


def scan_ssh_host_keys(host: str, port: int, timeout_seconds: int = 7) -> dict:
    host = validate_ssh_connection_host(host)
    scanner = shutil.which("ssh-keyscan")
    if not scanner:
        raise RuntimeError("ssh-keyscan is not installed on the management server")
    try:
        result = subprocess.run(
            [
                scanner, "-T", str(timeout_seconds), "-p", str(port),
                "-t", "ed25519,ecdsa,rsa", host,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 2,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("SSH host key scan timed out") from exc
    candidates = [
        line for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not candidates:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "no host key returned"
        raise RuntimeError(f"could not read SSH host key: {detail}")
    keys, fingerprints = validate_ssh_host_keys(host, port, candidates)
    return {"host": host, "port": port, "host_keys": keys, "fingerprints": fingerprints}


def public_key_from_private_file(path: str) -> str:
    private_key = Path(path)
    if not private_key.is_file():
        raise RuntimeError(f"SSH private key file does not exist: {private_key}")
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        raise RuntimeError("ssh-keygen is not installed on the management server")
    result = subprocess.run(
        [keygen, "-y", "-f", str(private_key)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    public_key = result.stdout.strip()
    if result.returncode != 0 or not public_key.startswith(tuple(SSH_KEY_TYPES)):
        raise RuntimeError("platform SSH private key cannot be read or requires a passphrase")
    return public_key
