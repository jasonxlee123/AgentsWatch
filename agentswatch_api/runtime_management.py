import hashlib
import json
import os
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from agentswatch_automation import AutomationRunner, OperationCatalog

from .database import WriteDatabase
from .operation_events import OperationEventRecorder, finalize_unhandled_run, record_system_event
from .server_management import ConflictError, NotFoundError, OperationService


RUNTIME_FIELDS = {
    "server_id", "runtime_family_id", "name", "deployment_mode", "lifecycle_status",
    "bind_address", "endpoint_port", "runtime_account", "config_path", "state_path",
    "command_path",
    "container_name", "image_ref", "desired_version", "management_driver", "management_target",
}
RUNTIME_STATUS_OPERATIONS = {
    "runtime.refresh_status", "runtime.start", "runtime.stop", "runtime.restart",
}
RUNTIME_MUTATION_OPERATIONS = {"runtime.start", "runtime.stop", "runtime.restart"}
RUNTIME_DEPLOYMENT_FIELDS = {
    "server_id", "runtime_family_id", "deployment_mode", "lifecycle_status",
    "config_path", "state_path", "management_driver", "management_target",
}
UNMANAGED_RUNTIME_METADATA_FIELDS = {
    "config_path", "state_path", "management_driver", "management_target",
}


def active_runtime_deployment_state(connection, runtime_id):
    return connection.execute(
        """
        SELECT count(*) AS active_deployment_count,
               sum(active.current_member_count) AS current_member_count,
               bool_and(
                   active.current_member_count > 0
                   AND active.all_members_unmanaged
               ) AS all_members_unmanaged
        FROM (
            SELECT deployment.agent_deployment_id,
                   count(member.deployment_member_id) AS current_member_count,
                   COALESCE(
                       bool_and(
                           member.mapping_status = 'unresolved'
                           AND member.runtime_agent_key IS NULL
                       ) FILTER (WHERE member.deployment_member_id IS NOT NULL),
                       false
                   ) AS all_members_unmanaged
            FROM asset.agent_deployment deployment
            LEFT JOIN asset.agent_deployment_member member
              ON member.agent_deployment_id = deployment.agent_deployment_id
             AND member.valid_to IS NULL
            WHERE deployment.runtime_id = %(runtime_id)s
              AND deployment.lifecycle_status = 'active'
            GROUP BY deployment.agent_deployment_id
        ) active
        HAVING count(*) > 0
        """,
        {"runtime_id": runtime_id},
    ).fetchone()


def unmanaged_runtime_metadata_update_allowed(
    active_deployment, before, desired, *, allowed_fields=None,
):
    """Allow control-metadata correction only for never-managed imports."""
    if not active_deployment:
        return True
    changed = {
        name for name in RUNTIME_DEPLOYMENT_FIELDS
        if name in desired and str(desired[name]) != str(before.get(name))
    }
    if not changed:
        return True
    allowed = set(allowed_fields or UNMANAGED_RUNTIME_METADATA_FIELDS)
    return bool(
        active_deployment.get("active_deployment_count")
        and active_deployment.get("current_member_count")
        and active_deployment.get("all_members_unmanaged")
        and changed.issubset(allowed)
    )


def validate_runtime_connection_policy(
    operation_code: str,
    connection_type: str,
    management_driver: str,
) -> None:
    """Reject local execution unless the explicit development switch is enabled."""
    local_allowed = os.environ.get("AGENTSWATCH_ALLOW_LOCAL_CONNECTION") == "true"
    if connection_type == "local" and not local_allowed:
        raise ValueError(f"{operation_code} 本机连接未启用")
    if management_driver == "mock" and not (connection_type == "local" and local_allowed):
        raise ValueError("mock 驱动只能用于已启用的本机测试连接")


def runtime_operation_become(operation_code: str, management_driver: str, use_become: bool) -> bool:
    """Return the least privilege needed for this runtime operation.

    A system service mutation is the only supported runtime operation that may
    elevate. User services and all read-only collection/status operations run
    as the configured SSH login user.
    """
    if management_driver == "systemd" and operation_code in RUNTIME_MUTATION_OPERATIONS:
        if not use_become:
            raise ValueError("systemd 系统服务变更要求宿主连接启用 sudo 提权")
        return True
    return False


class RuntimeManagementService:
    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _runtime_json(connection, runtime_id):
        row = connection.execute(
            "SELECT to_jsonb(r) AS value FROM asset.runtime_instance r WHERE runtime_id = %(runtime_id)s",
            {"runtime_id": runtime_id},
        ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _audit(connection, runtime_id, operation, fields, before, after, actor):
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value
            ) VALUES ('user', %(actor)s, 'ui', 'runtime_instance', %(runtime_id)s,
                      %(operation)s, %(fields)s, %(before)s, %(after)s)
            """,
            {
                "actor": actor, "runtime_id": runtime_id, "operation": operation,
                "fields": list(fields),
                "before": Jsonb(before) if before is not None else None,
                "after": Jsonb(after) if after is not None else None,
            },
        )

    @staticmethod
    def _validate_local_mock(values):
        if values.get("management_driver") == "mock" and os.environ.get("AGENTSWATCH_ALLOW_LOCAL_CONNECTION") != "true":
            raise ValueError("mock runtime management driver is disabled")

    @staticmethod
    def _active_operation(connection, runtime_id):
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
            {"lock_key": f"agent-runtime-deployment:{runtime_id}"},
        )
        return connection.execute(
            """
            SELECT run.run_id
            FROM automation.operation_host_result host
            JOIN automation.operation_run run ON run.run_id = host.run_id
            WHERE run.status IN ('queued', 'running')
              AND (
                host.runtime_id = %(runtime_id)s
                OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(host.target_plan -> 'locked_runtime_ids', '[]'::jsonb)
                    ) locked(value)
                    WHERE locked.value = %(runtime_id_text)s
                )
              )
            ORDER BY run.created_at DESC
            LIMIT 1
            """,
            {"runtime_id": runtime_id, "runtime_id_text": str(runtime_id)},
        ).fetchone()

    def create_runtime(self, values: dict[str, Any], actor="local-user"):
        self._validate_local_mock(values)
        columns = [name for name in RUNTIME_FIELDS if name in values]
        required = {"server_id", "runtime_family_id", "name", "deployment_mode"}
        if not required.issubset(columns):
            raise ValueError("server_id, runtime_family_id, name and deployment_mode are required")
        runtime_id = uuid4()
        params = {name: values[name] for name in columns}
        params["runtime_id"] = runtime_id
        with self.database.connection() as connection:
            connection.execute(
                f"INSERT INTO asset.runtime_instance (runtime_id, {', '.join(columns)}) "
                f"VALUES (%(runtime_id)s, {', '.join(f'%({name})s' for name in columns)})",
                params,
            )
            after = self._runtime_json(connection, runtime_id)
            self._audit(connection, runtime_id, "create", columns, None, after, actor)
        return runtime_id

    def update_runtime(self, runtime_id: UUID, values: dict[str, Any], expected_row_version: int, actor="local-user"):
        self._validate_local_mock(values)
        updates = {name: values[name] for name in RUNTIME_FIELDS if name in values}
        if not updates:
            raise ValueError("at least one runtime field is required")
        with self.database.connection() as connection:
            before = self._runtime_json(connection, runtime_id)
            if before is None:
                raise NotFoundError("runtime not found")
            active_operation = self._active_operation(connection, runtime_id)
            if active_operation:
                raise ConflictError(
                    f"runtime has an active task and cannot be edited: {active_operation['run_id']}"
                )
            if "server_id" in updates and str(updates["server_id"]) != str(before["server_id"]) and before["lifecycle_status"] != "planned":
                raise ConflictError("only a planned runtime can change its server")
            active_deployment = active_runtime_deployment_state(connection, runtime_id)
            if not unmanaged_runtime_metadata_update_allowed(
                active_deployment, before, updates,
            ):
                raise ConflictError(
                    "runtime has an active managed primary-agent deployment; migrate or remove it before changing deployment fields"
                )
            result = connection.execute(
                f"UPDATE asset.runtime_instance SET {', '.join(f'{name} = %({name})s' for name in updates)} "
                "WHERE runtime_id = %(runtime_id)s AND row_version = %(row_version)s",
                {**updates, "runtime_id": runtime_id, "row_version": expected_row_version},
            )
            if result.rowcount != 1:
                raise ConflictError("runtime has changed; reload and retry")
            after = self._runtime_json(connection, runtime_id)
            self._audit(connection, runtime_id, "update", updates, before, after, actor)

    def retire_runtime(self, runtime_id: UUID, expected_row_version: int, actor="local-user"):
        with self.database.connection() as connection:
            active = connection.execute(
                "SELECT count(*) AS count FROM asset.agent_deployment WHERE runtime_id = %(runtime_id)s AND lifecycle_status = 'active'",
                {"runtime_id": runtime_id},
            ).fetchone()["count"]
            if active:
                raise ConflictError("runtime has active primary-agent deployments and cannot be retired")
        self.update_runtime(runtime_id, {"lifecycle_status": "retired"}, expected_row_version, actor)


class RuntimeOperationService:
    def __init__(self, database=None, catalog=None):
        self.database = database or WriteDatabase()
        self.catalog = catalog or OperationCatalog()

    @staticmethod
    def _persist_discovery_snapshot(connection, runtime_id: UUID, result: dict[str, Any]):
        raw_payload = {
            key: result.get(key)
            for key in (
                "runtime_family_code", "actual_version", "command_path", "version_output",
                "config_path", "config_exists", "state_path", "state_exists",
                "endpoint_port", "endpoint_reachable", "observed_agent_count", "collected_at",
            )
        }
        extension_summary = {
            "command_path": raw_payload["command_path"],
            "version_output": raw_payload["version_output"],
        }
        config_summary = {
            "config_path": raw_payload["config_path"],
            "config_exists": raw_payload["config_exists"],
            "state_path": raw_payload["state_path"],
            "state_exists": raw_payload["state_exists"],
            "endpoint_port": raw_payload["endpoint_port"],
            "endpoint_reachable": raw_payload["endpoint_reachable"],
        }
        hash_payload = {
            "runtime_family_code": raw_payload["runtime_family_code"],
            "actual_version": raw_payload["actual_version"],
            "extensions": extension_summary,
            "config": config_summary,
            "observed_agent_count": raw_payload["observed_agent_count"],
        }
        content_hash = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        previous = connection.execute(
            """
            SELECT snapshot_id, content_hash
            FROM asset.runtime_discovery_snapshot
            WHERE runtime_id = %(runtime_id)s
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            {"runtime_id": runtime_id},
        ).fetchone()
        snapshot_created = previous is None or previous["content_hash"].strip() != content_hash
        snapshot_id = previous["snapshot_id"] if previous else None
        if snapshot_created:
            snapshot_id = uuid4()
            connection.execute(
                """
                INSERT INTO asset.runtime_discovery_snapshot (
                    snapshot_id, runtime_id, discovered_at, discovery_status, discovery_source,
                    actual_version, observed_agent_count, agent_count_confidence, content_hash,
                    extension_summary, config_summary, raw_payload
                ) VALUES (
                    %(snapshot_id)s, %(runtime_id)s, now(), 'succeeded', 'ansible_playbook',
                    %(actual_version)s, %(observed_agent_count)s, 'reported', %(content_hash)s,
                    %(extension_summary)s, %(config_summary)s, %(raw_payload)s
                )
                """,
                {
                    "snapshot_id": snapshot_id,
                    "runtime_id": runtime_id,
                    "actual_version": raw_payload["actual_version"],
                    "observed_agent_count": raw_payload["observed_agent_count"],
                    "content_hash": content_hash,
                    "extension_summary": Jsonb(extension_summary),
                    "config_summary": Jsonb(config_summary),
                    "raw_payload": Jsonb(raw_payload),
                },
            )
        connection.execute(
            """
            UPDATE asset.runtime_instance
            SET last_discovered_at = now(), last_operation_at = now()
            WHERE runtime_id = %(runtime_id)s
            """,
            {"runtime_id": runtime_id},
        )
        return {
            "snapshot_id": str(snapshot_id),
            "snapshot_created": snapshot_created,
            "content_hash": content_hash,
        }

    def create_run(self, operation_code, runtime_ids, parameters=None, requested_by="local-user"):
        operation = self.catalog.get(operation_code)
        if operation.scope != "runtime":
            raise ValueError("operation is not valid for runtimes")
        parameters = self.catalog.validate_parameters(operation_code, parameters)
        targets = list(dict.fromkeys(runtime_ids or []))
        if not targets:
            raise ValueError("at least one runtime target is required")
        if len(targets) > 1 and not operation.supports_batch:
            raise ValueError("operation does not support multiple runtime targets")
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT runtime_id, server_id FROM asset.runtime_instance
                WHERE runtime_id = ANY(%(runtime_ids)s)
                  AND lifecycle_status NOT IN ('disabled', 'retired')
                """,
                {"runtime_ids": targets},
            ).fetchall()
            if {row["runtime_id"] for row in rows} != set(targets):
                raise ValueError("one or more runtimes do not exist or are disabled/retired")
            OperationService._lock_target_servers(
                connection, [row["server_id"] for row in rows],
            )
            run_id = uuid4()
            connection.execute(
                """
                INSERT INTO automation.operation_run (
                    run_id, operation_code, target_type, parameters, requested_by,
                    target_count, playbook_version, automation_version_id
                ) VALUES (%(run_id)s, %(operation_code)s, 'runtime', %(parameters)s,
                          %(requested_by)s, %(target_count)s, %(playbook_version)s,
                          %(automation_version_id)s)
                """,
                {
                    "run_id": run_id, "operation_code": operation_code,
                    "parameters": Jsonb(parameters),
                    "requested_by": requested_by, "target_count": len(rows),
                    "playbook_version": OperationService._playbook_version(operation),
                    "automation_version_id": operation.version_id,
                },
            )
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO automation.operation_host_result (
                        run_id, server_id, runtime_id, target_key
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (run_id, row["server_id"], row["runtime_id"], str(row["runtime_id"])),
                )
        return run_id

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
                SELECT result.runtime_id, runtime.deployment_mode::text AS deployment_mode,
                       runtime.management_driver::text AS management_driver,
                       runtime.management_target, runtime.bind_address, runtime.endpoint_port,
                       runtime.config_path, runtime.state_path, runtime.command_path,
                       family.family_code,
                       connection.connection_type::text AS connection_type,
                       connection.host, connection.port, connection.username,
                       connection.credential_ref, connection.use_become,
                       connection.ssh_host_keys,
                       connection.bootstrap_status::text AS bootstrap_status,
                       connection.password_configured
                FROM automation.operation_host_result result
                JOIN asset.runtime_instance runtime ON runtime.runtime_id = result.runtime_id
                JOIN asset.runtime_family family ON family.runtime_family_id = runtime.runtime_family_id
                LEFT JOIN asset.server_connection connection ON connection.server_id = result.server_id
                WHERE result.run_id = %(run_id)s
                ORDER BY result.runtime_id
                """,
                {"run_id": run_id},
            ).fetchall()

        record_system_event(
            run_id, "task_started", "任务已开始执行，正在进行运行时预检。",
            phase="preflight", database=self.database,
        )

        credential_files = OperationService._credential_files()
        targets = []
        preflight_failures = {}
        for row in rows:
            runtime_id = str(row["runtime_id"])
            if row["connection_type"] is None:
                preflight_failures[runtime_id] = "宿主服务器未配置 Ansible 连接"
                continue
            if (
                row["connection_type"] == "ssh" and row["password_configured"]
                and row["bootstrap_status"] != "succeeded"
            ):
                preflight_failures[runtime_id] = "宿主服务器尚未完成密码建联和公钥复验"
                continue
            try:
                validate_runtime_connection_policy(
                    run["operation_code"], row["connection_type"], row["management_driver"],
                )
            except ValueError as exc:
                preflight_failures[runtime_id] = str(exc)
                continue
            if row["deployment_mode"] != "baremetal":
                preflight_failures[runtime_id] = "当前仅支持裸金属运行时操作"
                continue
            if run["operation_code"] != "runtime.collect_info" and (
                row["management_driver"] == "none" or not row["management_target"]
            ):
                preflight_failures[runtime_id] = "运行时未配置管理驱动"
                continue
            try:
                become = runtime_operation_become(
                    run["operation_code"], row["management_driver"], bool(row["use_become"]),
                )
            except ValueError as exc:
                preflight_failures[runtime_id] = str(exc)
                continue
            target = {
                "server_id": runtime_id,
                "address": row["host"],
                "connection": row["connection_type"],
                "port": row["port"],
                "user": row["username"],
                "become": become,
                "runtime_management_driver": row["management_driver"],
                "runtime_management_target": row["management_target"],
                "runtime_family_code": row["family_code"],
                "runtime_config_path": row["config_path"],
                "runtime_state_path": row["state_path"],
                "runtime_command_path": row["command_path"],
            }
            if row["ssh_host_keys"]:
                target["ssh_host_keys"] = list(row["ssh_host_keys"])
            if row["endpoint_port"] is not None:
                bind_address = str(row["bind_address"] or "").strip()
                target["runtime_endpoint_port"] = row["endpoint_port"]
                target["runtime_probe_host"] = (
                    "127.0.0.1"
                    if bind_address in {"", "0.0.0.0", "::", "::0", "127.0.0.1", "::1"}
                    else bind_address
                )
            credential_ref = row["credential_ref"]
            if credential_ref and credential_ref != "ssh-agent":
                key_file = credential_files.get(credential_ref)
                if not key_file:
                    preflight_failures[runtime_id] = f"凭据引用未解析: {credential_ref}"
                    continue
                target["private_key_file"] = key_file
            targets.append(target)

        report = None
        runner_error = None
        with OperationEventRecorder(run_id, self.database) as events:
            for runtime_id, error in preflight_failures.items():
                events.system(
                    "preflight_failed", error, phase="preflight", level="error",
                    target_key=runtime_id,
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
                except Exception as exc:
                    runner_error = str(exc)
                    events.system(
                        "ansible_error", runner_error, phase="ansible", level="error",
                    )

        statuses = {runtime_id: ("failed", None, error) for runtime_id, error in preflight_failures.items()}
        for target in targets:
            runtime_id = target["server_id"]
            if report is None:
                statuses[runtime_id] = ("failed", None, runner_error or "automation execution failed")
            else:
                host = report["hosts"][runtime_id]
                statuses[runtime_id] = (host["status"], host["result"], host["error"])

        with self.database.connection() as connection:
            for runtime_id, (status, result, error) in statuses.items():
                if status == "succeeded" and result and run["operation_code"] == "runtime.collect_info":
                    result = {
                        **result,
                        **self._persist_discovery_snapshot(connection, UUID(runtime_id), result),
                    }
                connection.execute(
                    """
                    UPDATE automation.operation_host_result
                    SET status = %(status)s, result = %(result)s, error_message = %(error)s,
                        started_at = COALESCE(started_at, now()), finished_at = now()
                    WHERE run_id = %(run_id)s AND runtime_id = %(runtime_id)s
                    """,
                    {
                        "status": status, "result": Jsonb(result) if result is not None else None,
                        "error": error, "run_id": run_id, "runtime_id": UUID(runtime_id),
                    },
                )
                if status == "succeeded" and result and run["operation_code"] in RUNTIME_STATUS_OPERATIONS:
                    connection.execute(
                        """
                        UPDATE asset.runtime_instance
                        SET observed_status = %(observed_status)s, last_operation_at = now()
                        WHERE runtime_id = %(runtime_id)s
                        """,
                        {"observed_status": result.get("observed_status", "unknown"), "runtime_id": UUID(runtime_id)},
                    )
                elif status == "unreachable":
                    connection.execute(
                        "UPDATE asset.runtime_instance SET observed_status = 'unreachable', last_operation_at = now() WHERE runtime_id = %(runtime_id)s",
                        {"runtime_id": UUID(runtime_id)},
                    )
            succeeded = sum(status == "succeeded" for status, _, _ in statuses.values())
            failed = len(statuses) - succeeded
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
