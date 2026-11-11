import atexit
import base64
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb

from .database import WriteDatabase
from .server_management import ConflictError, NotFoundError, OperationService


LOOPBACK_TUNNEL_HOSTS = {"127.0.0.1", "localhost"}


class CredentialCipher:
    def __init__(self, key: str | None = None):
        raw = (key or os.environ.get("AGENTSWATCH_WEB_CREDENTIAL_KEY") or "").strip()
        if not raw:
            raise RuntimeError("AGENTSWATCH_WEB_CREDENTIAL_KEY is required to save or use WebUI credentials")
        try:
            decoded = base64.urlsafe_b64decode(raw.encode())
            if len(decoded) != 32:
                raise ValueError
            self.fernet = Fernet(raw.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("AGENTSWATCH_WEB_CREDENTIAL_KEY must be a URL-safe base64 encoded 32-byte key") from exc

    def encrypt(self, payload: dict[str, str]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return self.fernet.encrypt(encoded).decode()

    def decrypt(self, ciphertext: str) -> dict[str, str]:
        try:
            value = json.loads(self.fernet.decrypt(ciphertext.encode()).decode())
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("saved WebUI credential cannot be decrypted with the configured key") from exc
        if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
            raise RuntimeError("saved WebUI credential payload is invalid")
        return value


class _ProcessRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._processes: dict[UUID, subprocess.Popen | None] = {}
        self._timers: dict[UUID, threading.Timer] = {}

    def register(self, session_id: UUID, process: subprocess.Popen | None, ttl_seconds: int, expire_callback):
        timer = threading.Timer(ttl_seconds, expire_callback, args=(session_id,))
        timer.daemon = True
        with self._lock:
            self._processes[session_id] = process
            self._timers[session_id] = timer
        timer.start()

    def take(self, session_id: UUID):
        with self._lock:
            process = self._processes.pop(session_id, None)
            timer = self._timers.pop(session_id, None)
        if timer:
            timer.cancel()
        return process

    @staticmethod
    def stop_process(process: subprocess.Popen | None):
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def shutdown(self):
        with self._lock:
            sessions = list(self._processes.values())
            timers = list(self._timers.values())
            self._processes.clear()
            self._timers.clear()
        for timer in timers:
            timer.cancel()
        for process in sessions:
            self.stop_process(process)


_REGISTRY = _ProcessRegistry()
atexit.register(_REGISTRY.shutdown)


class RuntimeWebAccessService:
    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _cipher():
        return CredentialCipher()

    @staticmethod
    def _validate_base_path(value: str):
        if not value.startswith("/") or value.startswith("//") or "?" in value or "#" in value:
            raise ValueError("base_path must be an absolute URL path without query or fragment")

    @staticmethod
    def _validate_direct_url(value: str | None):
        if not value:
            raise ValueError("direct_url is required for direct access")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("direct_url must be an http or https origin")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("direct_url must contain only scheme, host and optional port")

    @staticmethod
    def _sanitized_endpoint(row):
        if row is None:
            return None
        return {
            "runtime_web_endpoint_id": row["runtime_web_endpoint_id"],
            "runtime_id": row["runtime_id"],
            "endpoint_code": row["endpoint_code"],
            "display_name": row["display_name"],
            "access_mode": row["access_mode"],
            "scheme": row["scheme"],
            "direct_url": row["direct_url"],
            "remote_host": row["remote_host"],
            "port_override": row["port_override"],
            "base_path": row["base_path"],
            "auth_mode": row["auth_mode"],
            "credential_configured": bool(row.get("encrypted_credential")),
            "enabled": row["enabled"],
            "is_primary": row["is_primary"],
            "last_opened_at": row["last_opened_at"],
            "row_version": row["row_version"],
        }

    @staticmethod
    def _audit(connection, endpoint_id, runtime_id, operation, before, after, actor):
        before_value = RuntimeWebAccessService._sanitized_endpoint(before)
        after_value = RuntimeWebAccessService._sanitized_endpoint(after)
        changed_fields = sorted(
            key for key in set(before_value or {}) | set(after_value or {})
            if (before_value or {}).get(key) != (after_value or {}).get(key)
        )
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value, reason
            ) VALUES ('user', %(actor)s, 'ui', 'runtime_web_endpoint', %(endpoint_id)s,
                      %(operation)s, %(fields)s, %(before)s, %(after)s,
                      'runtime WebUI access configuration')
            """,
            {
                "actor": actor, "endpoint_id": endpoint_id, "runtime_id": runtime_id,
                "operation": operation, "fields": changed_fields,
                "before": Jsonb(json.loads(json.dumps(before_value, default=str))) if before_value is not None else None,
                "after": Jsonb(json.loads(json.dumps(after_value, default=str))) if after_value is not None else None,
            },
        )

    def _credential_payload(self, auth_mode, supplied, existing):
        if auth_mode == "none":
            return None
        previous = {}
        if existing and existing.get("auth_mode") == auth_mode and existing.get("encrypted_credential"):
            previous = self._cipher().decrypt(existing["encrypted_credential"])
        merged = {key: value for key, value in previous.items()}
        for key, value in supplied.items():
            if value is not None:
                merged[key] = value
        if auth_mode == "token":
            if not merged.get("token"):
                raise ValueError("token is required for token authentication")
            merged = {"token": merged["token"]}
        elif auth_mode == "password":
            if not merged.get("password"):
                raise ValueError("password is required for password authentication")
            merged = {"password": merged["password"]}
        elif auth_mode == "username_password":
            if not merged.get("username") or not merged.get("password"):
                raise ValueError("username and password are required")
            merged = {"username": merged["username"].strip(), "password": merged["password"]}
        else:
            raise ValueError("unsupported WebUI auth_mode")
        return self._cipher().encrypt(merged)

    def upsert_endpoint(self, runtime_id: UUID, values: dict[str, Any], credentials: dict[str, str | None], actor="local-user"):
        # This increment exposes exactly one managed Control UI endpoint per runtime.
        # Keep these identity fields server-owned so clients cannot create hidden alternates.
        values["endpoint_code"] = "control_ui"
        values["is_primary"] = True
        self._validate_base_path(values["base_path"])
        if values["access_mode"] in {"direct_url", "tailscale_serve"}:
            self._validate_direct_url(values.get("direct_url"))
            direct_scheme = urlsplit(values["direct_url"]).scheme
            if values["access_mode"] == "tailscale_serve" and direct_scheme != "https":
                raise ValueError("Tailscale Serve access requires an https URL")
            values["scheme"] = direct_scheme
            values["remote_host"] = "127.0.0.1"
            values["port_override"] = None
        else:
            values["direct_url"] = None
            if values["remote_host"] not in LOOPBACK_TUNNEL_HOSTS:
                raise ValueError("local SSH tunnel can only target remote loopback")

        with self.database.connection() as connection:
            runtime = connection.execute(
                "SELECT runtime_id, endpoint_port, lifecycle_status::text AS lifecycle_status "
                "FROM asset.runtime_instance WHERE runtime_id = %(runtime_id)s FOR UPDATE",
                {"runtime_id": runtime_id},
            ).fetchone()
            if runtime is None:
                raise NotFoundError("runtime not found")
            if runtime["lifecycle_status"] == "retired":
                raise ConflictError("retired runtime cannot configure WebUI access")
            if values["access_mode"] == "local_ssh_tunnel" and not (values.get("port_override") or runtime["endpoint_port"]):
                raise ValueError("runtime endpoint_port or WebUI port override is required")

            before = connection.execute(
                "SELECT *, access_mode::text AS access_mode, auth_mode::text AS auth_mode "
                "FROM asset.runtime_web_endpoint WHERE runtime_id = %(runtime_id)s AND is_primary FOR UPDATE",
                {"runtime_id": runtime_id},
            ).fetchone()
            expected_version = values.pop("row_version", None)
            if before and expected_version != before["row_version"]:
                raise ConflictError("runtime WebUI endpoint has changed; reload and retry")
            encrypted = self._credential_payload(values["auth_mode"], credentials, before)
            values["encrypted_credential"] = encrypted

            if before:
                endpoint_id = before["runtime_web_endpoint_id"]
                assignments = ", ".join(f"{name} = %({name})s" for name in values)
                result = connection.execute(
                    f"UPDATE asset.runtime_web_endpoint SET {assignments} "
                    "WHERE runtime_web_endpoint_id = %(endpoint_id)s AND row_version = %(expected_version)s",
                    {**values, "endpoint_id": endpoint_id, "expected_version": expected_version},
                )
                if result.rowcount != 1:
                    raise ConflictError("runtime WebUI endpoint has changed; reload and retry")
                operation = "update"
            else:
                endpoint_id = uuid4()
                columns = ["runtime_web_endpoint_id", "runtime_id", *values.keys()]
                params = {"runtime_web_endpoint_id": endpoint_id, "runtime_id": runtime_id, **values}
                connection.execute(
                    f"INSERT INTO asset.runtime_web_endpoint ({', '.join(columns)}) "
                    f"VALUES ({', '.join(f'%({name})s' for name in columns)})",
                    params,
                )
                operation = "create"
            after = connection.execute(
                "SELECT *, access_mode::text AS access_mode, auth_mode::text AS auth_mode "
                "FROM asset.runtime_web_endpoint WHERE runtime_web_endpoint_id = %(endpoint_id)s",
                {"endpoint_id": endpoint_id},
            ).fetchone()
            self._audit(connection, endpoint_id, runtime_id, operation, before, after, actor)
        return endpoint_id

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _wait_for_tunnel(process, port, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error = (process.stderr.read() if process.stderr else b"").decode(errors="replace").strip()
                raise RuntimeError(error or "SSH tunnel exited before becoming ready")
            with socket.socket() as probe:
                probe.settimeout(0.15)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.05)
        raise RuntimeError("SSH tunnel did not become ready before timeout")

    @staticmethod
    def _ssh_process(row, local_port):
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("ssh client is not installed")
        command = [
            ssh, "-N", "-L", f"127.0.0.1:{local_port}:{row['remote_host']}:{row['effective_port']}",
            "-p", str(row["connection_port"]),
            "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
        ]
        credential_ref = row["credential_ref"]
        if credential_ref and credential_ref != "ssh-agent":
            key_file = OperationService._credential_files().get(credential_ref)
            if not key_file or not Path(key_file).is_file():
                raise RuntimeError(f"credential reference cannot be resolved: {credential_ref}")
            command.extend(["-i", key_file])
        command.append(f"{row['connection_username']}@{row['connection_host']}")
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    @staticmethod
    def _append_path(origin: str, base_path: str):
        return origin.rstrip("/") + (base_path if base_path != "/" else "/")

    @staticmethod
    def _expire_session(session_id: UUID):
        try:
            RuntimeWebAccessService().close_session(session_id, "expired")
        except Exception:
            pass

    def create_session(self, runtime_id: UUID, requested_by="local-user"):
        ttl_seconds = int(os.environ.get("AGENTSWATCH_WEB_ACCESS_TTL_SECONDS", "900"))
        if not 30 <= ttl_seconds <= 86400:
            raise ValueError("AGENTSWATCH_WEB_ACCESS_TTL_SECONDS must be between 30 and 86400")
        session_id = uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        process = None
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT web.*, web.access_mode::text AS access_mode, web.auth_mode::text AS auth_mode,
                       COALESCE(web.port_override, runtime.endpoint_port) AS effective_port,
                       runtime.lifecycle_status::text AS runtime_status,
                       family.family_code, family.metadata AS family_metadata,
                       server_connection.connection_type::text AS connection_type,
                       server_connection.host AS connection_host,
                       server_connection.port AS connection_port,
                       server_connection.username AS connection_username,
                       server_connection.credential_ref
                FROM asset.runtime_web_endpoint web
                JOIN asset.runtime_instance runtime ON runtime.runtime_id = web.runtime_id
                JOIN asset.runtime_family family ON family.runtime_family_id = runtime.runtime_family_id
                LEFT JOIN asset.server_connection server_connection ON server_connection.server_id = runtime.server_id
                WHERE web.runtime_id = %(runtime_id)s AND web.is_primary
                """,
                {"runtime_id": runtime_id},
            ).fetchone()
            if row is None:
                raise NotFoundError("runtime WebUI endpoint is not configured")
            if not row["enabled"]:
                raise ConflictError("runtime WebUI endpoint is disabled")
            if row["runtime_status"] in {"disabled", "retired"}:
                raise ConflictError("disabled or retired runtime cannot be opened")
            if row["access_mode"] == "local_ssh_tunnel" and not row["effective_port"]:
                raise ValueError("runtime WebUI port is not configured")
            connection.execute(
                """
                INSERT INTO automation.runtime_web_access_session (
                    access_session_id, runtime_web_endpoint_id, runtime_id, requested_by,
                    access_mode, expires_at
                ) VALUES (%(session_id)s, %(endpoint_id)s, %(runtime_id)s, %(requested_by)s,
                          %(access_mode)s, %(expires_at)s)
                """,
                {
                    "session_id": session_id, "endpoint_id": row["runtime_web_endpoint_id"],
                    "runtime_id": runtime_id, "requested_by": requested_by,
                    "access_mode": row["access_mode"], "expires_at": expires_at,
                },
            )

        try:
            local_port = None
            if row["access_mode"] in {"direct_url", "tailscale_serve"}:
                launch_url = self._append_path(row["direct_url"], row["base_path"])
            elif row["connection_type"] == "local":
                local_port = row["effective_port"]
                launch_url = self._append_path(f"{row['scheme']}://127.0.0.1:{local_port}", row["base_path"])
            else:
                if row["connection_type"] != "ssh":
                    raise RuntimeError("host server does not have an SSH connection configured")
                local_port = self._free_port()
                process = self._ssh_process(row, local_port)
                self._wait_for_tunnel(process, local_port)
                launch_url = self._append_path(f"{row['scheme']}://127.0.0.1:{local_port}", row["base_path"])

            manual_credential = None
            if row["auth_mode"] != "none":
                credential = self._cipher().decrypt(row["encrypted_credential"])
                adapter = (row["family_metadata"] or {}).get("web_ui", {})
                token_delivery = adapter.get("token_delivery")
                if token_delivery is None and row["family_code"] == "openclaw":
                    token_delivery = "url_fragment"
                if row["auth_mode"] == "token" and token_delivery == "url_fragment":
                    launch_url = f"{launch_url}#{urlencode({'token': credential['token']})}"
                else:
                    manual_credential = {"auth_mode": row["auth_mode"], **credential}

            with self.database.connection() as connection:
                connection.execute(
                    """
                    UPDATE automation.runtime_web_access_session
                    SET status = 'active', local_port = %(local_port)s,
                        process_id = %(process_id)s, activated_at = now()
                    WHERE access_session_id = %(session_id)s
                    """,
                    {"local_port": local_port, "process_id": process.pid if process else None, "session_id": session_id},
                )
                connection.execute(
                    "UPDATE asset.runtime_web_endpoint SET last_opened_at = now() "
                    "WHERE runtime_web_endpoint_id = %(endpoint_id)s",
                    {"endpoint_id": row["runtime_web_endpoint_id"]},
                )
            _REGISTRY.register(session_id, process, ttl_seconds, self._expire_session)
            return {
                "access_session_id": session_id,
                "runtime_id": runtime_id,
                "status": "active",
                "launch_url": launch_url,
                "manual_credential": manual_credential,
                "expires_at": expires_at,
            }
        except Exception as exc:
            _REGISTRY.stop_process(process)
            with self.database.connection() as connection:
                connection.execute(
                    """
                    UPDATE automation.runtime_web_access_session
                    SET status = 'failed', failure_reason = %(error)s, closed_at = now()
                    WHERE access_session_id = %(session_id)s
                    """,
                    {"error": str(exc)[:2000], "session_id": session_id},
                )
            raise

    def close_session(self, session_id: UUID, status="closed"):
        if status not in {"closed", "expired"}:
            raise ValueError("invalid WebUI session close status")
        process = _REGISTRY.take(session_id)
        _REGISTRY.stop_process(process)
        with self.database.connection() as connection:
            result = connection.execute(
                """
                UPDATE automation.runtime_web_access_session
                SET status = %(status)s, closed_at = now()
                WHERE access_session_id = %(session_id)s
                  AND status IN ('opening', 'active')
                RETURNING access_session_id, runtime_id, status::text AS status,
                          created_at, activated_at, expires_at, closed_at
                """,
                {"status": status, "session_id": session_id},
            ).fetchone()
            if result is None:
                existing = connection.execute(
                    """
                    SELECT access_session_id, runtime_id, status::text AS status,
                           created_at, activated_at, expires_at, closed_at
                    FROM automation.runtime_web_access_session
                    WHERE access_session_id = %(session_id)s
                    """,
                    {"session_id": session_id},
                ).fetchone()
                if existing is None:
                    raise NotFoundError("runtime WebUI access session not found")
                return existing
            return result
