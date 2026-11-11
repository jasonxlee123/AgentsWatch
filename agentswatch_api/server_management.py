import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from agentswatch_automation import AutomationRunner, OperationCatalog

from .database import WriteDatabase
from .operation_events import OperationEventRecorder, finalize_unhandled_run, record_system_event
from .server_credentials import (
    ServerCredentialCipher,
    public_key_from_private_file,
    validate_ssh_connection_host,
    validate_ssh_host_keys,
)


ROOT = Path(__file__).resolve().parents[1]
SERVER_FIELDS = {
    "environment_id", "cloud_provider_id", "provider_instance_id", "name",
    "lifecycle_status", "cpu_cores", "memory_gib", "disk_gib", "internal_ip",
    "public_ip", "os_name", "os_version", "architecture", "work_account",
}


class ConflictError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


class ServerManagementService:
    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _write_audit(connection, server_id, operation, changed_fields, before_value, after_value, actor):
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value
            ) VALUES ('user', %(actor)s, 'ui', 'server_asset', %(server_id)s,
                      %(operation)s, %(changed_fields)s, %(before_value)s, %(after_value)s)
            """,
            {
                "actor": actor,
                "server_id": server_id,
                "operation": operation,
                "changed_fields": list(changed_fields),
                "before_value": Jsonb(before_value) if before_value is not None else None,
                "after_value": Jsonb(after_value) if after_value is not None else None,
            },
        )

    @staticmethod
    def _server_json(connection, server_id):
        row = connection.execute(
            "SELECT to_jsonb(s) AS value FROM asset.server_asset s WHERE server_id = %(server_id)s",
            {"server_id": server_id},
        ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _active_operation(connection, server_id):
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%(lock_key)s, 0))",
            {"lock_key": f"automation-server:{server_id}"},
        )
        return connection.execute(
            """
            SELECT run.run_id
            FROM automation.operation_host_result host
            JOIN automation.operation_run run ON run.run_id = host.run_id
            WHERE run.status IN ('queued', 'running')
              AND (
                host.server_id = %(server_id)s
                OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(host.target_plan -> 'locked_server_ids', '[]'::jsonb)
                    ) locked(value)
                    WHERE locked.value = %(server_id_text)s
                )
              )
            ORDER BY run.created_at DESC
            LIMIT 1
            """,
            {"server_id": server_id, "server_id_text": str(server_id)},
        ).fetchone()

    @staticmethod
    def _assert_server_can_leave_service(connection, server_id):
        active_runtime = connection.execute(
            """
            SELECT runtime_id FROM asset.runtime_instance
            WHERE server_id = %(server_id)s AND lifecycle_status = 'active'
            ORDER BY runtime_id LIMIT 1
            """,
            {"server_id": server_id},
        ).fetchone()
        if active_runtime:
            raise ConflictError(
                f"server has active runtime {active_runtime['runtime_id']}; retire or move the runtime first"
            )

    @staticmethod
    def _replace_groups(connection, server_id, group_ids):
        requested = {UUID(str(value)) for value in group_ids}
        if requested:
            rows = connection.execute(
                """
                SELECT group_id FROM asset.management_group
                WHERE group_id = ANY(%(group_ids)s) AND resource_type = 'server'
                  AND lifecycle_status = 'active'
                """,
                {"group_ids": list(requested)},
            ).fetchall()
            found = {row["group_id"] for row in rows}
            if found != requested:
                raise ValueError("one or more server management groups do not exist or are inactive")
        current_rows = connection.execute(
            "SELECT group_id FROM asset.server_group_member WHERE server_id = %(server_id)s AND valid_to IS NULL",
            {"server_id": server_id},
        ).fetchall()
        current = {row["group_id"] for row in current_rows}
        removed = current - requested
        added = requested - current
        if removed:
            connection.execute(
                """
                UPDATE asset.server_group_member SET valid_to = now()
                WHERE server_id = %(server_id)s AND valid_to IS NULL AND group_id = ANY(%(group_ids)s)
                """,
                {"server_id": server_id, "group_ids": list(removed)},
            )
        for group_id in added:
            connection.execute(
                "INSERT INTO asset.server_group_member (group_id, server_id) VALUES (%s, %s)",
                (group_id, server_id),
            )

    def create_server(self, values: dict[str, Any], actor="local-user"):
        group_ids = values.pop("group_ids", [])
        columns = [name for name in SERVER_FIELDS if name in values]
        if "name" not in columns or "environment_id" not in columns:
            raise ValueError("name and environment_id are required")
        server_id = uuid4()
        placeholders = [f"%({name})s" for name in columns]
        params = {name: values[name] for name in columns}
        params["server_id"] = server_id
        with self.database.connection() as connection:
            connection.execute(
                f"INSERT INTO asset.server_asset (server_id, {', '.join(columns)}) "
                f"VALUES (%(server_id)s, {', '.join(placeholders)})",
                params,
            )
            self._replace_groups(connection, server_id, group_ids)
            after = self._server_json(connection, server_id)
            self._write_audit(connection, server_id, "create", columns + ["group_ids"], None, after, actor)
        return server_id

    def update_server(self, server_id: UUID, values: dict[str, Any], expected_row_version: int, actor="local-user"):
        group_ids = values.pop("group_ids", None)
        updates = {name: values[name] for name in SERVER_FIELDS if name in values}
        if not updates and group_ids is None:
            raise ValueError("at least one server field or group_ids is required")
        with self.database.connection() as connection:
            before = self._server_json(connection, server_id)
            if before is None:
                raise NotFoundError("server not found")
            active_operation = self._active_operation(connection, server_id)
            if active_operation:
                raise ConflictError(
                    f"server has an active task and cannot be edited: {active_operation['run_id']}"
                )
            if updates.get("lifecycle_status") in {"disabled", "retired"}:
                self._assert_server_can_leave_service(connection, server_id)
            if updates:
                assignments = ", ".join(f"{name} = %({name})s" for name in updates)
                result = connection.execute(
                    f"UPDATE asset.server_asset SET {assignments} "
                    "WHERE server_id = %(server_id)s AND row_version = %(row_version)s",
                    {**updates, "server_id": server_id, "row_version": expected_row_version},
                )
                if result.rowcount != 1:
                    raise ConflictError("server has changed; reload and retry")
            elif before["row_version"] != expected_row_version:
                raise ConflictError("server has changed; reload and retry")
            if group_ids is not None:
                self._replace_groups(connection, server_id, group_ids)
            after = self._server_json(connection, server_id)
            changed = list(updates) + (["group_ids"] if group_ids is not None else [])
            self._write_audit(connection, server_id, "update", changed, before, after, actor)

    def retire_server(self, server_id: UUID, expected_row_version: int, actor="local-user"):
        self.update_server(server_id, {"lifecycle_status": "retired"}, expected_row_version, actor)

    def save_connection(self, server_id: UUID, values: dict[str, Any], actor="local-user"):
        supplied_password = values.pop("ssh_password", None)
        supplied_host_keys = values.pop("ssh_host_keys", None)
        if values.get("connection_type") == "ssh":
            values["host"] = validate_ssh_connection_host(values.get("host"))
        with self.database.connection() as connection:
            active_operation = self._active_operation(connection, server_id)
            if active_operation:
                raise ConflictError(
                    f"server has an active task and its connection cannot be edited: {active_operation['run_id']}"
                )
            if not connection.execute(
                "SELECT 1 FROM asset.server_asset WHERE server_id = %(server_id)s",
                {"server_id": server_id},
            ).fetchone():
                raise NotFoundError("server not found")
            existing = connection.execute(
                """
                SELECT connection.row_version, connection.connection_type::text AS connection_type,
                       connection.host, connection.port, connection.username,
                       connection.credential_ref, connection.use_become,
                       credential.encrypted_password AS encrypted_ssh_password,
                       ssh_host_keys, ssh_host_key_fingerprints,
                       bootstrap_status::text AS bootstrap_status, bootstrap_completed_at,
                       password_updated_at
                FROM asset.server_connection connection
                LEFT JOIN security.server_ssh_credential credential
                  ON credential.server_id = connection.server_id
                WHERE connection.server_id = %(server_id)s
                """,
                {"server_id": server_id},
            ).fetchone()
            expected = values.pop("row_version", None)
            if existing and expected != existing["row_version"]:
                raise ConflictError("server connection has changed; reload and retry")

            connection_type = values["connection_type"]
            password_changed = supplied_password is not None
            if connection_type == "local":
                values.update(port=None, username=None, credential_ref=None)
                encrypted_password = None
                host_keys, fingerprints = [], []
                bootstrap_status = "not_required"
                bootstrap_completed_at = None
            else:
                values["credential_ref"] = str(values.get("credential_ref") or "").strip() or None
                encrypted_password = existing["encrypted_ssh_password"] if existing else None
                if password_changed:
                    encrypted_password = ServerCredentialCipher().encrypt(supplied_password)
                if encrypted_password and (
                    not values["credential_ref"] or values["credential_ref"] == "ssh-agent"
                ):
                    raise ValueError("password bootstrap requires a platform SSH key credential reference")

                endpoint_unchanged = bool(
                    existing
                    and existing["connection_type"] == "ssh"
                    and existing["host"] == values["host"]
                    and existing["port"] == values["port"]
                )
                if supplied_host_keys is None and endpoint_unchanged:
                    host_keys = list(existing["ssh_host_keys"] or [])
                    fingerprints = list(existing["ssh_host_key_fingerprints"] or [])
                elif supplied_host_keys:
                    host_keys, fingerprints = validate_ssh_host_keys(
                        values["host"], values["port"], supplied_host_keys,
                    )
                else:
                    host_keys, fingerprints = [], []
                if encrypted_password and not host_keys:
                    raise ValueError("scan and confirm the SSH host fingerprint before password bootstrap")

                bootstrap_inputs_changed = bool(
                    password_changed
                    or not existing
                    or not endpoint_unchanged
                    or existing["username"] != values["username"]
                    or existing["credential_ref"] != values["credential_ref"]
                    or list(existing["ssh_host_keys"] or []) != host_keys
                )
                if encrypted_password and bootstrap_inputs_changed:
                    bootstrap_status = "pending"
                    bootstrap_completed_at = None
                elif encrypted_password:
                    bootstrap_status = existing["bootstrap_status"]
                    bootstrap_completed_at = existing["bootstrap_completed_at"]
                else:
                    # Existing key-only connections remain valid without a password bootstrap.
                    bootstrap_status = "succeeded"
                    bootstrap_completed_at = existing["bootstrap_completed_at"] if existing else None

            if existing:
                result = connection.execute(
                    """
                    UPDATE asset.server_connection
                    SET connection_type = %(connection_type)s, host = %(host)s,
                        port = %(port)s, username = %(username)s,
                        credential_ref = %(credential_ref)s, use_become = %(use_become)s,
                        password_configured = %(password_configured)s,
                        ssh_host_keys = %(host_keys)s,
                        ssh_host_key_fingerprints = %(fingerprints)s,
                        bootstrap_status = %(bootstrap_status)s,
                        bootstrap_error = NULL,
                        bootstrap_completed_at = %(bootstrap_completed_at)s,
                        password_updated_at = CASE WHEN %(password_changed)s THEN now() ELSE password_updated_at END,
                        connection_status = 'unknown', last_error = NULL
                    WHERE server_id = %(server_id)s AND row_version = %(row_version)s
                    """,
                    {
                        **values, "server_id": server_id, "row_version": expected,
                        "password_configured": bool(encrypted_password),
                        "host_keys": Jsonb(host_keys), "fingerprints": Jsonb(fingerprints),
                        "bootstrap_status": bootstrap_status,
                        "bootstrap_completed_at": bootstrap_completed_at,
                        "password_changed": password_changed,
                    },
                )
                if result.rowcount != 1:
                    raise ConflictError("server connection has changed; reload and retry")
                operation = "update"
            else:
                connection.execute(
                    """
                    INSERT INTO asset.server_connection (
                        server_id, connection_type, host, port, username, credential_ref, use_become,
                        password_configured, ssh_host_keys, ssh_host_key_fingerprints,
                        bootstrap_status, password_updated_at
                    ) VALUES (%(server_id)s, %(connection_type)s, %(host)s, %(port)s,
                              %(username)s, %(credential_ref)s, %(use_become)s,
                              %(password_configured)s, %(host_keys)s, %(fingerprints)s,
                              %(bootstrap_status)s, CASE WHEN %(password_changed)s THEN now() ELSE NULL END)
                    """,
                    {
                        **values, "server_id": server_id,
                        "password_configured": bool(encrypted_password),
                        "host_keys": Jsonb(host_keys), "fingerprints": Jsonb(fingerprints),
                        "bootstrap_status": bootstrap_status,
                        "password_changed": password_changed,
                    },
                )
                operation = "link"
            if encrypted_password:
                connection.execute(
                    """
                    INSERT INTO security.server_ssh_credential (server_id, encrypted_password)
                    VALUES (%(server_id)s, %(encrypted_password)s)
                    ON CONFLICT (server_id) DO UPDATE
                    SET encrypted_password = EXCLUDED.encrypted_password, updated_at = now()
                    """,
                    {"server_id": server_id, "encrypted_password": encrypted_password},
                )
            else:
                connection.execute(
                    "DELETE FROM security.server_ssh_credential WHERE server_id = %(server_id)s",
                    {"server_id": server_id},
                )
            self._write_audit(
                connection, server_id, operation, ["connection"], None,
                {
                    "connection_type": values["connection_type"], "host": values["host"],
                    "port": values["port"], "username": values["username"],
                    "use_become": values["use_become"], "password_configured": bool(encrypted_password),
                    "host_key_fingerprints": fingerprints, "bootstrap_status": bootstrap_status,
                }, actor,
            )


class OperationService:
    def __init__(self, database=None, catalog=None):
        self.database = database or WriteDatabase()
        self.catalog = catalog or OperationCatalog()

    @staticmethod
    def _playbook_version(operation):
        checksum = hashlib.sha256()
        automation_root = operation.playbook.parents[2]
        for path in (operation.playbook, *operation.source_paths):
            checksum.update(str(path.relative_to(automation_root)).encode())
            checksum.update(path.read_bytes())
        digest = checksum.hexdigest()[:12]
        return f"{operation.version}+{digest}"

    @staticmethod
    def _lock_target_servers(connection, server_ids):
        server_ids = sorted(set(server_ids), key=str)
        for server_id in server_ids:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%(lock_key)s, 0))",
                {"lock_key": f"automation-server:{server_id}"},
            )
        active = connection.execute(
            """
            SELECT host.server_id, run.run_id, run.operation_code
            FROM automation.operation_host_result host
            JOIN automation.operation_run run ON run.run_id = host.run_id
            WHERE run.status IN ('queued', 'running')
              AND (
                host.server_id = ANY(%(server_ids)s)
                OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(host.target_plan -> 'locked_server_ids', '[]'::jsonb)
                    ) locked(value)
                    WHERE locked.value = ANY(%(server_id_texts)s)
                )
              )
            ORDER BY run.created_at, host.server_id
            LIMIT 1
            """,
            {
                "server_ids": server_ids,
                "server_id_texts": [str(value) for value in server_ids],
            },
        ).fetchone()
        if active:
            raise ConflictError(
                f"server {active['server_id']} already has an active task: {active['run_id']}"
            )

    def create_run(self, operation_code, server_ids=None, group_id=None, parameters=None, requested_by="local-user"):
        operation = self.catalog.get(operation_code)
        if operation.scope != "server":
            raise ValueError("operation is not valid for servers")
        parameters = self.catalog.validate_parameters(operation_code, parameters)
        with self.database.connection() as connection:
            if group_id is not None:
                rows = connection.execute(
                    """
                    SELECT s.server_id
                    FROM asset.server_group_member m
                    JOIN asset.server_asset s ON s.server_id = m.server_id
                    WHERE m.group_id = %(group_id)s AND m.valid_to IS NULL
                      AND s.lifecycle_status NOT IN ('disabled', 'retired')
                    ORDER BY s.server_id
                    """,
                    {"group_id": group_id},
                ).fetchall()
                targets = [row["server_id"] for row in rows]
            else:
                targets = list(dict.fromkeys(server_ids or []))
            if not targets:
                raise ValueError("at least one executable server target is required")
            if len(targets) > 1 and not operation.supports_batch:
                raise ValueError("operation does not support multiple server targets")
            existing = connection.execute(
                """
                SELECT server_id FROM asset.server_asset
                WHERE server_id = ANY(%(server_ids)s) AND lifecycle_status NOT IN ('disabled', 'retired')
                """,
                {"server_ids": targets},
            ).fetchall()
            if {row["server_id"] for row in existing} != set(targets):
                raise ValueError("one or more servers do not exist or are disabled/retired")
            self._lock_target_servers(connection, targets)
            run_id = uuid4()
            connection.execute(
                """
                INSERT INTO automation.operation_run (
                    run_id, operation_code, target_type, parameters, requested_by,
                    target_count, playbook_version, automation_version_id
                ) VALUES (%(run_id)s, %(operation_code)s, 'server', %(parameters)s, %(requested_by)s,
                          %(target_count)s, %(playbook_version)s, %(automation_version_id)s)
                """,
                {
                    "run_id": run_id,
                    "operation_code": operation_code,
                    "parameters": Jsonb(parameters or {}),
                    "requested_by": requested_by,
                    "target_count": len(targets),
                    "playbook_version": self._playbook_version(operation),
                    "automation_version_id": operation.version_id,
                },
            )
            for server_id in targets:
                connection.execute(
                    "INSERT INTO automation.operation_host_result (run_id, server_id, target_key) VALUES (%s, %s, %s)",
                    (run_id, server_id, str(server_id)),
                )
        return run_id

    @staticmethod
    def _credential_files():
        raw = os.environ.get("AGENTSWATCH_CREDENTIAL_FILE_MAP", "{}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("AGENTSWATCH_CREDENTIAL_FILE_MAP must be a JSON object")
        default_file = os.environ.get("AGENTSWATCH_DEFAULT_SSH_PRIVATE_KEY_FILE", "").strip()
        if default_file:
            default_ref = os.environ.get(
                "AGENTSWATCH_DEFAULT_SSH_CREDENTIAL_REF", "agentswatch-managed",
            ).strip()
            if not default_ref:
                raise ValueError("AGENTSWATCH_DEFAULT_SSH_CREDENTIAL_REF cannot be empty")
            value.setdefault(default_ref, default_file)
        return value

    def execute_run(self, run_id: UUID):
        try:
            return self._execute_run(run_id)
        except Exception as exc:
            finalize_unhandled_run(run_id, exc, self.database)

    def _execute_run(self, run_id: UUID):
        with self.database.connection() as connection:
            run = connection.execute(
                """
                SELECT operation_code, parameters, status::text AS status
                FROM automation.operation_run WHERE run_id = %(run_id)s FOR UPDATE
                """,
                {"run_id": run_id},
            ).fetchone()
            if run is None or run["status"] != "queued":
                return
            connection.execute(
                "UPDATE automation.operation_run SET status = 'running', started_at = now() WHERE run_id = %(run_id)s",
                {"run_id": run_id},
            )
            rows = connection.execute(
                """
                SELECT h.server_id, s.internal_ip, c.connection_type::text AS connection_type,
                       c.host, c.port, c.username, c.credential_ref, c.use_become,
                       credential.encrypted_password AS encrypted_ssh_password, c.ssh_host_keys,
                       c.bootstrap_status::text AS bootstrap_status
                FROM automation.operation_host_result h
                JOIN asset.server_asset s ON s.server_id = h.server_id
                LEFT JOIN asset.server_connection c ON c.server_id = h.server_id
                LEFT JOIN security.server_ssh_credential credential
                  ON credential.server_id = h.server_id
                WHERE h.run_id = %(run_id)s
                ORDER BY h.server_id
                """,
                {"run_id": run_id},
            ).fetchall()
            if run["operation_code"] == "server.bootstrap_ssh":
                connection.execute(
                    """
                    UPDATE asset.server_connection connection
                    SET bootstrap_status = 'running', bootstrap_error = NULL
                    FROM automation.operation_host_result host
                    WHERE host.run_id = %(run_id)s
                      AND host.server_id = connection.server_id
                      AND connection.connection_type = 'ssh'
                    """,
                    {"run_id": run_id},
                )

        record_system_event(
            run_id, "task_started", "任务已开始执行，正在进行目标预检。",
            phase="preflight", database=self.database,
        )

        credential_files = self._credential_files()
        bootstrap = run["operation_code"] == "server.bootstrap_ssh"
        targets = []
        verification_targets = {}
        preflight_failures = {}
        for row in rows:
            server_id = str(row["server_id"])
            if row["connection_type"] is None:
                preflight_failures[server_id] = "未配置 Ansible 连接"
                continue
            if row["connection_type"] == "local" and os.environ.get("AGENTSWATCH_ALLOW_LOCAL_CONNECTION") != "true":
                preflight_failures[server_id] = "本机连接未启用"
                continue
            if row["connection_type"] == "ssh":
                try:
                    validate_ssh_connection_host(row["host"] or str(row["internal_ip"] or ""))
                except ValueError as exc:
                    preflight_failures[server_id] = str(exc)
                    continue
            if bootstrap and row["connection_type"] != "ssh":
                preflight_failures[server_id] = "首次纳管只适用于 SSH 服务器"
                continue
            if (
                not bootstrap and row["connection_type"] == "ssh"
                and row["encrypted_ssh_password"]
                and row["bootstrap_status"] != "succeeded"
            ):
                preflight_failures[server_id] = "服务器尚未完成密码建联和公钥复验"
                continue
            target = {
                "server_id": server_id,
                "address": row["host"] or str(row["internal_ip"] or ""),
                "connection": row["connection_type"],
                "port": row["port"],
                "user": row["username"],
                "become": row["use_become"],
            }
            if row["ssh_host_keys"]:
                target["ssh_host_keys"] = list(row["ssh_host_keys"])
            credential_ref = row["credential_ref"]
            if bootstrap:
                if not row["encrypted_ssh_password"]:
                    preflight_failures[server_id] = "未保存服务器 SSH 登录密码"
                    continue
                if not credential_ref or credential_ref == "ssh-agent":
                    preflight_failures[server_id] = "未配置可下发的平台 SSH 公钥凭据"
                    continue
                key_file = credential_files.get(credential_ref)
                if not key_file:
                    preflight_failures[server_id] = f"凭据引用未解析: {credential_ref}"
                    continue
                try:
                    password = ServerCredentialCipher().decrypt(row["encrypted_ssh_password"])
                    public_key = public_key_from_private_file(key_file)
                except Exception as exc:
                    preflight_failures[server_id] = str(exc)
                    continue
                target["password"] = password
                if row["use_become"]:
                    target["become_password"] = password
                target["agentswatch_bootstrap_public_key"] = public_key
                verification_target = {
                    key: value for key, value in target.items()
                    if key not in {"password", "become_password", "agentswatch_bootstrap_public_key"}
                }
                verification_target["private_key_file"] = key_file
                verification_target["become"] = False
                verification_targets[server_id] = verification_target
            elif credential_ref and credential_ref != "ssh-agent":
                key_file = credential_files.get(credential_ref)
                if not key_file:
                    preflight_failures[server_id] = f"凭据引用未解析: {credential_ref}"
                    continue
                target["private_key_file"] = key_file
            targets.append(target)

        report = None
        runner_error = None
        with OperationEventRecorder(run_id, self.database) as events:
            for server_id, error in preflight_failures.items():
                events.system(
                    "preflight_failed", error, phase="preflight", level="error",
                    target_key=server_id,
                )
            if targets:
                try:
                    events.system(
                        "ansible_started", f"开始执行 Playbook：{run['operation_code']}",
                        phase="ansible",
                    )
                    report = AutomationRunner(self.catalog).run(
                        run["operation_code"], targets, run["parameters"],
                        run_id=str(run_id), event_callback=events.record,
                    )
                    if bootstrap:
                        successful_ids = [
                            target["server_id"] for target in targets
                            if report["hosts"][target["server_id"]]["status"] == "succeeded"
                        ]
                        if successful_ids:
                            events.system(
                                "public_key_verification_started",
                                "平台公钥已写入，正在断开密码认证并执行仅公钥复验。",
                                # Verification is a second Ansible execution;
                                # keep the phase within the persisted event contract.
                                phase="ansible",
                            )
                            verification = AutomationRunner(self.catalog).run(
                                "server.check_connection",
                                [verification_targets[server_id] for server_id in successful_ids],
                                {}, run_id=str(run_id), event_callback=events.record,
                            )
                            for server_id in successful_ids:
                                verified = verification["hosts"][server_id]
                                if verified["status"] == "succeeded":
                                    report["hosts"][server_id] = {
                                        "status": "succeeded",
                                        "result": {
                                            "managed": True,
                                            "authentication": "public_key",
                                            "password_retained_for_recovery": True,
                                        },
                                        "error": None,
                                    }
                                else:
                                    report["hosts"][server_id] = {
                                        "status": "failed", "result": None,
                                        "error": f"平台公钥复验失败: {verified['error'] or 'connection failed'}",
                                    }
                except Exception as exc:  # execution failures must be persisted per host
                    runner_error = str(exc)
                    report = None
                    events.system(
                        "ansible_error", runner_error, phase="ansible", level="error",
                    )

        with self.database.connection() as connection:
            now_statuses = {}
            for server_id, error in preflight_failures.items():
                now_statuses[server_id] = ("failed", None, error)
            for target in targets:
                server_id = target["server_id"]
                if report is None:
                    now_statuses[server_id] = ("failed", None, runner_error or "automation execution failed")
                else:
                    host = report["hosts"][server_id]
                    now_statuses[server_id] = (host["status"], host["result"], host["error"])
            for server_id, (status, result, error) in now_statuses.items():
                connection.execute(
                    """
                    UPDATE automation.operation_host_result
                    SET status = %(status)s, result = %(result)s, error_message = %(error)s,
                        started_at = COALESCE(started_at, now()), finished_at = now()
                    WHERE run_id = %(run_id)s AND server_id = %(server_id)s
                    """,
                    {
                        "status": status, "result": Jsonb(result) if result is not None else None,
                        "error": error, "run_id": run_id, "server_id": UUID(server_id),
                    },
                )
                if run["operation_code"] == "server.bootstrap_ssh":
                    connection_status = (
                        "reachable" if status == "succeeded"
                        else "auth_failed" if "permission denied" in str(error or "").lower()
                        else "unreachable"
                    )
                    connection.execute(
                        """
                        UPDATE asset.server_connection
                        SET bootstrap_status = %(bootstrap_status)s,
                            bootstrap_error = %(error)s,
                            bootstrap_completed_at = CASE WHEN %(succeeded)s THEN now() ELSE NULL END,
                            connection_status = %(connection_status)s,
                            last_checked_at = now(), last_error = %(error)s
                        WHERE server_id = %(server_id)s
                        """,
                        {
                            "bootstrap_status": "succeeded" if status == "succeeded" else "failed",
                            "succeeded": status == "succeeded", "connection_status": connection_status,
                            "error": error, "server_id": UUID(server_id),
                        },
                    )
                elif run["operation_code"] == "server.check_connection":
                    connection.execute(
                        """
                        UPDATE asset.server_connection
                        SET connection_status = %(connection_status)s, last_checked_at = now(), last_error = %(error)s
                        WHERE server_id = %(server_id)s
                        """,
                        {
                            "connection_status": "reachable" if status == "succeeded" else "unreachable",
                            "error": error, "server_id": UUID(server_id),
                        },
                    )
                elif status == "succeeded" and run["operation_code"] == "server.collect_facts":
                    connection.execute(
                        "UPDATE asset.server_connection SET last_facts = %(result)s, last_facts_at = now() WHERE server_id = %(server_id)s",
                        {"result": Jsonb(result), "server_id": UUID(server_id)},
                    )
                elif status == "succeeded" and run["operation_code"] == "server.discover_runtimes":
                    connection.execute(
                        "UPDATE asset.server_connection SET last_runtime_discovery = %(result)s, last_runtime_discovery_at = now() WHERE server_id = %(server_id)s",
                        {"result": Jsonb(result), "server_id": UUID(server_id)},
                    )
            succeeded = sum(status == "succeeded" for status, _, _ in now_statuses.values())
            failed = len(now_statuses) - succeeded
            final_status = "succeeded" if failed == 0 else "failed" if succeeded == 0 else "partial"
            connection.execute(
                """
                UPDATE automation.operation_run
                SET status = %(status)s, succeeded_count = %(succeeded)s,
                    failed_count = %(failed)s, finished_at = now()
                WHERE run_id = %(run_id)s
                """,
                {"status": final_status, "succeeded": succeeded, "failed": failed, "run_id": run_id},
            )
        record_system_event(
            run_id, "task_completed",
            f"任务执行结束：成功 {succeeded}，失败 {failed}。",
            phase="finalize", level="error" if final_status == "failed" else "warning" if final_status == "partial" else "info",
            data={"status": final_status, "succeeded": succeeded, "failed": failed},
            database=self.database,
        )
