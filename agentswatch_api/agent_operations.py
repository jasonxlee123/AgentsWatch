import json
import os
import re
import tempfile
from pathlib import PurePosixPath
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from agentswatch_automation import AutomationRunner, OperationCatalog

from .database import WriteDatabase
from .operation_events import OperationEventRecorder, finalize_unhandled_run, record_system_event
from .runtime_web_access import CredentialCipher
from .server_management import ConflictError, OperationService


RUNTIME_AGENT_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class AgentOperationService:
    def __init__(self, database=None, catalog=None):
        self.database = database or WriteDatabase()
        self.catalog = catalog or OperationCatalog()

    @staticmethod
    def _workspace_root(value, state_path):
        root = str(value or "").strip() or str(PurePosixPath(state_path) / "workspaces")
        if not PurePosixPath(root).is_absolute():
            raise ValueError("workspace_root must be an absolute path")
        return root.rstrip("/")

    @staticmethod
    def _child_key(primary_key, agent_id):
        suffix = str(agent_id).replace("-", "")[:8]
        prefix = primary_key[:51].rstrip("_-")
        return f"{prefix}-sub-{suffix}"

    @staticmethod
    def _channel_account_id(agent_id):
        return f"aw-{str(agent_id).replace('-', '')}"

    @classmethod
    def _definitions(
        cls, connection, primary_agent, runtime, parameters, *, rebase_workspace=False,
    ):
        existing = {
            row["agent_id"]: row
            for row in connection.execute(
                """
                SELECT member.agent_id, member.runtime_agent_key, member.config_ref,
                       member.mapping_status::text AS mapping_status,
                       agent.name, agent.lifecycle_status::text AS lifecycle_status
                FROM asset.agent_deployment deployment
                JOIN asset.agent_deployment_member member
                  ON member.agent_deployment_id = deployment.agent_deployment_id
                 AND member.valid_to IS NULL
                JOIN asset.agent_asset agent ON agent.agent_id = member.agent_id
                WHERE deployment.primary_agent_id = %(agent_id)s
                  AND deployment.lifecycle_status = 'active'
                """,
                {"agent_id": primary_agent["agent_id"]},
            ).fetchall()
        }
        rows = connection.execute(
            """
            SELECT agent.agent_id, agent.name, agent.structure_role::text AS structure_role,
                   COALESCE(channel.channel_type::text, 'none') AS channel_type,
                   channel.agent_channel_configuration_id AS channel_configuration_id,
                   channel.application_name AS channel_application_name,
                   channel.app_id AS channel_app_id,
                   channel.row_version AS channel_row_version
            FROM asset.agent_asset agent
            LEFT JOIN asset.agent_channel_configuration channel
              ON channel.agent_id = agent.agent_id AND channel.enabled
            WHERE agent.agent_id = %(agent_id)s
               OR (agent.parent_agent_id = %(agent_id)s AND agent.lifecycle_status <> 'retired')
            ORDER BY CASE WHEN agent.structure_role = 'primary' THEN 0 ELSE 1 END,
                     agent.name, agent.agent_id
            """,
            {"agent_id": primary_agent["agent_id"]},
        ).fetchall()
        root = cls._workspace_root(parameters.get("workspace_root"), runtime["state_path"])
        primary_key = parameters["runtime_agent_key"].strip()
        current_primary = existing.get(primary_agent["agent_id"])
        if (
            current_primary
            and current_primary["runtime_agent_key"]
            and current_primary["mapping_status"] == "verified"
            and current_primary["runtime_agent_key"] != primary_key
        ):
            raise ConflictError("runtime_agent_key cannot change for an active deployment")
        definitions = []
        for row in rows:
            current = existing.get(row["agent_id"])
            runtime_key = (
                primary_key
                if row["structure_role"] == "primary"
                and current
                and current["mapping_status"] != "verified"
                else current["runtime_agent_key"] if current and current["runtime_agent_key"]
                else primary_key if row["structure_role"] == "primary"
                else cls._child_key(primary_key, row["agent_id"])
            )
            definitions.append({
                "agent_id": str(row["agent_id"]),
                "name": row["name"],
                "structure_role": row["structure_role"],
                "runtime_agent_key": runtime_key,
                "workspace_path": (
                    current["config_ref"]
                    if current and current["config_ref"]
                    and current["runtime_agent_key"] == runtime_key
                    and not rebase_workspace
                    else f"{root}/{runtime_key}"
                ),
                "model": str(parameters.get("model") or "").strip(),
                "channel_type": row["channel_type"],
                "channel_account_id": cls._channel_account_id(row["agent_id"]),
                "channel_configuration_id": str(row["channel_configuration_id"]) if row["channel_configuration_id"] else None,
                "channel_application_name": row["channel_application_name"],
                "channel_app_id": row["channel_app_id"],
                "channel_row_version": row["channel_row_version"],
            })
        runtime_keys = [item["runtime_agent_key"] for item in definitions]
        if any(
            not runtime_key or not RUNTIME_AGENT_KEY.fullmatch(runtime_key)
            for runtime_key in runtime_keys
        ):
            raise ConflictError(
                "one or more deployment members has an invalid runtime agent key"
            )
        if len(runtime_keys) != len(set(runtime_keys)):
            raise ConflictError(
                "deployment members must use unique runtime agent keys"
            )
        desired_ids = {UUID(item["agent_id"]) for item in definitions}
        retired_definitions = [
            {
                "agent_id": str(agent_id),
                "name": row["name"],
                "runtime_agent_key": row["runtime_agent_key"],
                "channel_account_id": cls._channel_account_id(agent_id),
            }
            for agent_id, row in existing.items()
            if agent_id not in desired_ids and row["runtime_agent_key"]
        ]
        return definitions, retired_definitions

    @staticmethod
    def _deployment_member_state(connection, deployment_id):
        return list(connection.execute(
            """
            SELECT agent_id, member_role::text AS member_role, runtime_agent_key,
                   mapping_status::text AS mapping_status, config_ref
            FROM asset.agent_deployment_member
            WHERE agent_deployment_id = %(deployment_id)s AND valid_to IS NULL
            ORDER BY CASE WHEN member_role = 'primary' THEN 0 ELSE 1 END, deployment_member_id
            """,
            {"deployment_id": deployment_id},
        ).fetchall())

    @staticmethod
    def _members_are_verified(member_state):
        return bool(member_state) and all(
            item["mapping_status"] == "verified"
            and bool(item["runtime_agent_key"])
            for item in member_state
        )

    @staticmethod
    def _assert_active_deployment_has_members(active, member_state):
        if active is None:
            return
        if not member_state:
            raise ConflictError(
                "active deployment has no current members; repair or remove the orphaned relationship before remote operations"
            )
        primary_members = [
            item for item in member_state if item["member_role"] == "primary"
        ]
        if (
            len(primary_members) != 1
            or primary_members[0]["agent_id"] != active["primary_agent_id"]
        ):
            raise ConflictError(
                "active deployment has an invalid current primary member; repair the orphaned relationship before remote operations"
            )

    @classmethod
    def _deployment_matches_definitions(cls, connection, deployment_id, definitions):
        members = cls._deployment_member_state(connection, deployment_id)
        if not cls._members_are_verified(members):
            return False
        expected = {
            (
                UUID(item["agent_id"]), item["structure_role"],
                item["runtime_agent_key"],
            )
            for item in definitions
        }
        actual = {
            (
                item["agent_id"], item["member_role"],
                item["runtime_agent_key"],
            )
            for item in members
        }
        return len(members) == len(definitions) and expected == actual and all(
            item["config_ref"]
            and PurePosixPath(item["config_ref"]).is_absolute()
            for item in members
        )

    @classmethod
    def _deployment_cleanup_definitions(cls, connection, deployment_id):
        return [
            {
                "agent_id": str(row["agent_id"]),
                "name": row["name"],
                "runtime_agent_key": row["runtime_agent_key"],
                "workspace_path": row["config_ref"],
                "structure_role": row["member_role"],
                "channel_account_id": cls._channel_account_id(row["agent_id"]),
            }
            for row in connection.execute(
                """
                SELECT member.agent_id, member.runtime_agent_key, member.config_ref,
                       member.member_role::text AS member_role, agent.name
                FROM asset.agent_deployment_member member
                JOIN asset.agent_asset agent ON agent.agent_id = member.agent_id
                WHERE member.agent_deployment_id = %(deployment_id)s
                  AND member.valid_to IS NULL
                  AND member.runtime_agent_key IS NOT NULL
                ORDER BY CASE WHEN member.member_role = 'primary' THEN 0 ELSE 1 END,
                         agent.name, member.deployment_member_id
                """,
                {"deployment_id": deployment_id},
            ).fetchall()
        ]

    @classmethod
    def _deployment_restore_definitions(cls, connection, deployment_id):
        return [
            {
                "agent_id": str(row["agent_id"]),
                "name": row["name"],
                "structure_role": row["member_role"],
                "runtime_agent_key": row["runtime_agent_key"],
                "workspace_path": row["config_ref"],
                "model": "",
                "channel_type": row["channel_type"],
                "channel_account_id": cls._channel_account_id(row["agent_id"]),
                "channel_configuration_id": (
                    str(row["channel_configuration_id"])
                    if row["channel_configuration_id"] else None
                ),
                "channel_application_name": row["channel_application_name"],
                "channel_app_id": row["channel_app_id"],
                "channel_row_version": row["channel_row_version"],
            }
            for row in connection.execute(
                """
                SELECT member.agent_id, member.member_role::text AS member_role,
                       member.runtime_agent_key, member.config_ref, agent.name,
                       COALESCE(channel.channel_type::text, 'none') AS channel_type,
                       channel.agent_channel_configuration_id AS channel_configuration_id,
                       channel.application_name AS channel_application_name,
                       channel.app_id AS channel_app_id,
                       channel.row_version AS channel_row_version
                FROM asset.agent_deployment_member member
                JOIN asset.agent_asset agent ON agent.agent_id = member.agent_id
                LEFT JOIN asset.agent_channel_configuration channel
                  ON channel.agent_id = member.agent_id AND channel.enabled
                WHERE member.agent_deployment_id = %(deployment_id)s
                  AND member.valid_to IS NULL
                  AND member.mapping_status = 'verified'
                  AND member.runtime_agent_key IS NOT NULL
                  AND member.config_ref IS NOT NULL
                ORDER BY CASE WHEN member.member_role = 'primary' THEN 0 ELSE 1 END,
                         agent.name, member.deployment_member_id
                """,
                {"deployment_id": deployment_id},
            ).fetchall()
        ]

    @staticmethod
    def _assert_source_synchronized(cleanup_definitions, desired_definitions, retired_definitions):
        current = {
            (item["agent_id"], item["runtime_agent_key"])
            for item in cleanup_definitions
        }
        desired = {
            (item["agent_id"], item["runtime_agent_key"])
            for item in desired_definitions
        }
        if retired_definitions or current != desired:
            raise ConflictError(
                "source deployment members are not synchronized; run deployment sync before migrating or removing"
            )

    @staticmethod
    def _assert_no_foreign_active_members(connection, primary_agent_id, definitions):
        agent_ids = [UUID(item["agent_id"]) for item in definitions]
        conflict = connection.execute(
            """
            SELECT member.agent_id, deployment.primary_agent_id,
                   deployment.agent_deployment_id
            FROM asset.agent_deployment_member member
            JOIN asset.agent_deployment deployment
              ON deployment.agent_deployment_id = member.agent_deployment_id
            WHERE member.agent_id = ANY(%(agent_ids)s)
              AND member.valid_to IS NULL
              AND deployment.lifecycle_status = 'active'
              AND deployment.primary_agent_id <> %(primary_agent_id)s
            ORDER BY member.agent_id, deployment.agent_deployment_id
            LIMIT 1
            """,
            {"agent_ids": agent_ids, "primary_agent_id": primary_agent_id},
        ).fetchone()
        if conflict:
            raise ConflictError(
                "an agent is still linked to another primary agent's active deployment; "
                "synchronize the previous primary deployment before applying this relationship"
            )

    @staticmethod
    def _parse_uuid_parameter(parameters, name):
        try:
            return UUID(parameters[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a UUID") from exc

    @staticmethod
    def _runtime(connection, runtime_id):
        return connection.execute(
            """
            SELECT runtime.runtime_id, runtime.server_id, runtime.row_version,
                   runtime.deployment_mode::text AS deployment_mode,
                   runtime.lifecycle_status::text AS lifecycle_status,
                   runtime.config_path, runtime.state_path, runtime.endpoint_port,
                   runtime.management_driver::text AS management_driver,
                   runtime.management_target, family.family_code,
                   server.lifecycle_status::text AS server_lifecycle_status
            FROM asset.runtime_instance runtime
            JOIN asset.runtime_family family ON family.runtime_family_id = runtime.runtime_family_id
            JOIN asset.server_asset server ON server.server_id = runtime.server_id
            WHERE runtime.runtime_id = %(runtime_id)s
            """,
            {"runtime_id": runtime_id},
        ).fetchone()

    @staticmethod
    def _validate_runtime(runtime, *, label="target"):
        if runtime is None:
            raise ValueError(f"{label} runtime does not exist")
        if (
            runtime["lifecycle_status"] != "active"
            or runtime["server_lifecycle_status"] not in {"active", "maintenance"}
        ):
            raise ConflictError(f"{label} runtime and server must be active")
        if runtime["deployment_mode"] != "baremetal" or runtime["family_code"] != "openclaw":
            raise ValueError("agent deployment currently supports baremetal OpenClaw runtimes only")
        if not runtime["config_path"] or not runtime["state_path"]:
            raise ValueError(f"{label} runtime requires config_path and state_path")
        if (
            runtime["management_driver"] not in {"systemd", "systemd_user", "launchd", "mock"}
            or not runtime["management_target"]
        ):
            raise ValueError(f"{label} runtime requires a managed service before agent deployment")

    @staticmethod
    def _runtime_snapshot(runtime):
        return {
            key: str(runtime[key]) if key in {"runtime_id", "server_id"} else runtime[key]
            for key in (
                "runtime_id", "server_id", "row_version", "config_path", "state_path",
                "endpoint_port", "management_driver", "management_target", "family_code",
                "deployment_mode", "lifecycle_status", "server_lifecycle_status",
            )
        }

    @staticmethod
    def _lock_agent_runtimes(connection, runtime_ids):
        runtime_ids = sorted(set(runtime_ids), key=str)
        for runtime_id in runtime_ids:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
                {"lock_key": f"agent-runtime-deployment:{runtime_id}"},
            )
        active = connection.execute(
            """
            SELECT run.run_id
            FROM automation.operation_host_result host
            JOIN automation.operation_run run ON run.run_id = host.run_id
            WHERE run.status IN ('queued', 'running')
              AND (
                host.runtime_id = ANY(%(runtime_ids)s)
                OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(host.target_plan -> 'locked_runtime_ids', '[]'::jsonb)
                    ) locked(value)
                    WHERE locked.value = ANY(%(runtime_id_texts)s)
                )
              )
            ORDER BY run.created_at DESC
            LIMIT 1
            """,
            {
                "runtime_ids": runtime_ids,
                "runtime_id_texts": [str(value) for value in runtime_ids],
            },
        ).fetchone()
        if active:
            raise ConflictError(f"runtime already has an active task: {active['run_id']}")

    @staticmethod
    def _active_deployment(connection, agent_id):
        return connection.execute(
            """
            SELECT agent_deployment_id, primary_agent_id, runtime_id,
                   lifecycle_status::text AS lifecycle_status, row_version
            FROM asset.agent_deployment
            WHERE primary_agent_id = %(agent_id)s AND lifecycle_status = 'active'
            """,
            {"agent_id": agent_id},
        ).fetchone()

    @staticmethod
    def _occupied_runtime(connection, runtime_id, agent_id):
        return connection.execute(
            """
            SELECT deployment.agent_deployment_id, agent.agent_id, agent.name AS agent_name
            FROM asset.agent_deployment deployment
            JOIN asset.agent_asset agent ON agent.agent_id = deployment.primary_agent_id
            WHERE deployment.runtime_id = %(runtime_id)s
              AND deployment.lifecycle_status = 'active'
              AND deployment.primary_agent_id <> %(agent_id)s
            """,
            {"runtime_id": runtime_id, "agent_id": agent_id},
        ).fetchone()

    def create_run(self, operation_code, agent_ids, parameters, requested_by="local-user"):
        operation = self.catalog.get(operation_code)
        if operation.scope != "agent":
            raise ValueError("operation is not valid for agents")
        if operation.code not in {
            "agent.apply_deployment", "agent.migrate_deployment", "agent.remove_deployment",
        }:
            raise ValueError("agent automation does not have a supported execution handler")
        parameters = self.catalog.validate_parameters(operation_code, parameters)
        targets = list(dict.fromkeys(agent_ids or []))
        if len(targets) != 1:
            raise ValueError("agent deployment operations require exactly one primary agent")

        runtime_id = None
        expected_deployment_id = None
        runtime_agent_key = None
        if operation_code in {"agent.apply_deployment", "agent.migrate_deployment"}:
            runtime_id = self._parse_uuid_parameter(parameters, "runtime_id")
            runtime_agent_key = parameters["runtime_agent_key"].strip()
            if not RUNTIME_AGENT_KEY.fullmatch(runtime_agent_key):
                raise ValueError("runtime_agent_key contains unsupported characters")
            parameters = {
                **parameters, "runtime_id": str(runtime_id), "runtime_agent_key": runtime_agent_key,
            }
        if operation_code in {"agent.migrate_deployment", "agent.remove_deployment"}:
            expected_deployment_id = self._parse_uuid_parameter(parameters, "expected_deployment_id")
            parameters = {**parameters, "expected_deployment_id": str(expected_deployment_id)}

        with self.database.connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
                {"lock_key": f"agent-operation:{targets[0]}"},
            )
            active_run = connection.execute(
                """
                SELECT run.run_id
                FROM automation.operation_host_result host
                JOIN automation.operation_run run ON run.run_id = host.run_id
                WHERE host.agent_id = %(agent_id)s
                  AND run.status IN ('queued', 'running')
                ORDER BY run.created_at DESC
                LIMIT 1
                """,
                {"agent_id": targets[0]},
            ).fetchone()
            if active_run:
                raise ConflictError(f"agent already has an active task: {active_run['run_id']}")
            agent = connection.execute(
                """
                SELECT agent_id, name, structure_role::text AS structure_role,
                       lifecycle_status::text AS lifecycle_status
                FROM asset.agent_asset WHERE agent_id = %(agent_id)s
                """,
                {"agent_id": targets[0]},
            ).fetchone()
            if agent is None or agent["structure_role"] != "primary":
                raise ValueError("deployment target must be a primary agent")
            if operation_code != "agent.remove_deployment" and agent["lifecycle_status"] not in {"testing", "active"}:
                raise ConflictError("primary agent must be testing or active before deployment")

            active = self._active_deployment(connection, agent["agent_id"])
            plan = {"mode": operation_code.rsplit(".", 1)[-1]}
            deployment_id = active["agent_deployment_id"] if active else None

            if operation_code == "agent.apply_deployment":
                runtime = self._runtime(connection, runtime_id)
                self._validate_runtime(runtime)
                self._lock_agent_runtimes(connection, [runtime_id])
                OperationService._lock_target_servers(connection, [runtime["server_id"]])
                occupied = self._occupied_runtime(connection, runtime_id, agent["agent_id"])
                if occupied:
                    raise ConflictError(
                        f"target runtime is already assigned to primary agent "
                        f"{occupied['agent_name']} ({occupied['agent_id']})"
                    )
                if active and active["runtime_id"] != runtime_id:
                    raise ConflictError("use agent.migrate_deployment to change an active deployment runtime")
                require_existing_agent_keys = []
                allow_existing_agent_keys = []
                member_state = []
                if active:
                    member_state = self._deployment_member_state(
                        connection, active["agent_deployment_id"],
                    )
                    self._assert_active_deployment_has_members(
                        active, member_state,
                    )
                    nonverified = [
                        item for item in member_state
                        if item["mapping_status"] != "verified"
                    ]
                    if any(
                        item["member_role"] != "primary"
                        for item in nonverified
                    ):
                        raise ConflictError(
                            "this imported deployment has unresolved child members; "
                            "multi-member adoption is not supported in this version; ask an "
                            "administrator to repair the imported mapping, or use metadata-only "
                            "removal only when every member is completely unresolved"
                        )
                    require_existing_agent_keys = list(dict.fromkeys(
                        [
                            runtime_agent_key
                            if item["member_role"] == "primary"
                            else item["runtime_agent_key"]
                            for item in nonverified
                        ]
                    ))
                    allow_existing_agent_keys = list(dict.fromkeys(
                        item["runtime_agent_key"]
                        for item in member_state
                        if item["mapping_status"] == "verified"
                        and item["runtime_agent_key"]
                    ))
                definitions, retired_definitions = self._definitions(
                    connection, agent, runtime, parameters,
                )
                desired_runtime_keys = {
                    item["runtime_agent_key"] for item in definitions
                }
                allow_existing_agent_keys = [
                    key for key in allow_existing_agent_keys
                    if key in desired_runtime_keys
                ]
                self._assert_no_foreign_active_members(
                    connection, agent["agent_id"], definitions,
                )
                if active and self._members_are_verified(member_state):
                    source_apply_definitions = self._deployment_restore_definitions(
                        connection, active["agent_deployment_id"],
                    )
                    if len(source_apply_definitions) != len(member_state):
                        raise ConflictError(
                            "active deployment members lack verified workspace metadata; "
                            "repair the relationship before synchronizing it"
                        )
                    source_primary = next(
                        item for item in source_apply_definitions
                        if item["structure_role"] == "primary"
                    )
                    source_agent_ids = {
                        item["agent_id"] for item in source_apply_definitions
                    }
                    apply_compensation = {
                        "mode": "restore_existing",
                        "source_apply_definitions": source_apply_definitions,
                        "source_retired_definitions": [
                            item for item in definitions
                            if item["agent_id"] not in source_agent_ids
                        ],
                        "source_apply_parameters": {
                            "runtime_id": str(runtime_id),
                            "runtime_agent_key": source_primary["runtime_agent_key"],
                        },
                    }
                else:
                    apply_compensation = {"mode": "cleanup_unconfirmed"}
                plan.update({
                    "definitions": definitions,
                    "retired_definitions": retired_definitions,
                    "target_runtime": self._runtime_snapshot(runtime),
                    "require_existing_agent_keys": require_existing_agent_keys,
                    "allow_existing_agent_keys": allow_existing_agent_keys,
                    "apply_compensation": apply_compensation,
                    "cleanup_reference_id": str(
                        active["agent_deployment_id"] if active else uuid4()
                    ),
                    "expected_deployment_row_version": (
                        active["row_version"] if active else None
                    ),
                    "locked_runtime_ids": [str(runtime_id)],
                    "locked_server_ids": [str(runtime["server_id"])],
                })
                host_runtime, host_server = runtime_id, runtime["server_id"]

            else:
                expected = connection.execute(
                    """
                    SELECT agent_deployment_id, primary_agent_id, runtime_id,
                           lifecycle_status::text AS lifecycle_status, row_version
                    FROM asset.agent_deployment
                    WHERE agent_deployment_id = %(deployment_id)s
                      AND primary_agent_id = %(agent_id)s
                    """,
                    {"deployment_id": expected_deployment_id, "agent_id": agent["agent_id"]},
                ).fetchone()
                if expected is None:
                    raise ConflictError("expected deployment no longer belongs to this primary agent")

                if operation_code == "agent.remove_deployment":
                    if expected["lifecycle_status"] != "active":
                        if active is not None:
                            raise ConflictError("the expected deployment ended and another deployment is active")
                        if self._deployment_member_state(
                            connection, expected_deployment_id,
                        ):
                            raise ConflictError(
                                "the expected deployment ended with current members still attached"
                            )
                        plan.update({"mode": "already_removed", "expected_deployment_id": str(expected_deployment_id)})
                        source = self._runtime(connection, expected["runtime_id"])
                    else:
                        if active is None or active["agent_deployment_id"] != expected_deployment_id:
                            raise ConflictError("active deployment changed; reload and retry")
                        source = self._runtime(connection, expected["runtime_id"])
                        if source is None:
                            raise ConflictError("source runtime no longer exists")
                        member_state = self._deployment_member_state(
                            connection, expected_deployment_id,
                        )
                        self._assert_active_deployment_has_members(
                            expected, member_state,
                        )
                        legacy_unmanaged = bool(member_state) and all(
                            item["mapping_status"] == "unresolved"
                            and item["runtime_agent_key"] is None
                            for item in member_state
                        )
                        has_unresolved_members = not self._members_are_verified(
                            member_state,
                        )
                        if has_unresolved_members and not legacy_unmanaged:
                            raise ConflictError(
                                "source deployment mixes resolved and unresolved runtime keys; "
                                "run runtime discovery and synchronize every member before removing"
                            )
                        if legacy_unmanaged:
                            plan.update({
                                "mode": "remove_unmanaged",
                                "expected_deployment_id": str(expected_deployment_id),
                                "expected_deployment_row_version": expected["row_version"],
                                "source_runtime": self._runtime_snapshot(source),
                                "source_cleanup_definitions": [],
                            })
                        else:
                            self._validate_runtime(source, label="source")
                        primary_key_row = connection.execute(
                            """
                            SELECT runtime_agent_key FROM asset.agent_deployment_member
                            WHERE agent_deployment_id = %(deployment_id)s
                              AND member_role = 'primary' AND valid_to IS NULL
                            """,
                            {"deployment_id": expected_deployment_id},
                        ).fetchone()
                        if primary_key_row is None:
                            raise ConflictError("active deployment has no current primary member")
                        if not legacy_unmanaged:
                            if not primary_key_row["runtime_agent_key"]:
                                raise ConflictError(
                                    "source deployment has unresolved runtime keys; run deployment sync with the confirmed OpenClaw key first"
                                )
                            source_parameters = {
                                "runtime_id": str(source["runtime_id"]),
                                "runtime_agent_key": primary_key_row["runtime_agent_key"],
                            }
                            source_apply, source_retired = self._definitions(
                                connection, agent, source, source_parameters,
                            )
                            source_cleanup = self._deployment_cleanup_definitions(
                                connection, expected_deployment_id,
                            )
                            self._assert_source_synchronized(
                                source_cleanup, source_apply, source_retired,
                            )
                            plan.update({
                                "expected_deployment_id": str(expected_deployment_id),
                                "expected_deployment_row_version": expected["row_version"],
                                "source_runtime": self._runtime_snapshot(source),
                                "source_cleanup_definitions": source_cleanup,
                                "source_apply_definitions": source_apply,
                                "source_retired_definitions": source_retired,
                                "source_apply_parameters": source_parameters,
                            })
                    locked_runtimes = [source["runtime_id"]]
                    locked_servers = [source["server_id"]]
                    host_runtime, host_server = source["runtime_id"], source["server_id"]

                else:
                    target = self._runtime(connection, runtime_id)
                    self._validate_runtime(target)
                    if expected["lifecycle_status"] != "active":
                        if (
                            active is not None
                            and active["runtime_id"] == runtime_id
                            and expected["runtime_id"] != runtime_id
                        ):
                            target_definitions, target_retired = self._definitions(
                                connection, agent, target, parameters,
                                rebase_workspace=True,
                            )
                            if not self._deployment_matches_definitions(
                                connection, active["agent_deployment_id"],
                                target_definitions,
                            ):
                                raise ConflictError(
                                    "the committed target deployment no longer matches this migration request"
                                )
                            plan.update({
                                "mode": "already_migrated",
                                "expected_deployment_id": str(expected_deployment_id),
                                "active_deployment_id": str(active["agent_deployment_id"]),
                                "target_runtime": self._runtime_snapshot(target),
                                "definitions": target_definitions,
                                "retired_definitions": target_retired,
                            })
                            source = self._runtime(connection, expected["runtime_id"])
                        else:
                            raise ConflictError("expected deployment is no longer the active source relationship")
                    else:
                        if active is None or active["agent_deployment_id"] != expected_deployment_id:
                            raise ConflictError("active deployment changed; reload and retry")
                        if expected["runtime_id"] == runtime_id:
                            raise ConflictError("target runtime must differ from the active source runtime")
                        source = self._runtime(connection, expected["runtime_id"])
                        if source is None:
                            raise ConflictError("source runtime no longer exists")
                        member_state = self._deployment_member_state(
                            connection, expected_deployment_id,
                        )
                        self._assert_active_deployment_has_members(
                            expected, member_state,
                        )
                        if not self._members_are_verified(member_state):
                            raise ConflictError(
                                "source deployment has unresolved runtime keys; "
                                "run deployment sync with confirmed OpenClaw keys before migrating"
                            )
                        self._validate_runtime(source, label="source")
                        occupied = self._occupied_runtime(connection, runtime_id, agent["agent_id"])
                        if occupied:
                            raise ConflictError(
                                f"target runtime is already assigned to primary agent "
                                f"{occupied['agent_name']} ({occupied['agent_id']})"
                            )
                        primary_key_row = connection.execute(
                            """
                            SELECT runtime_agent_key FROM asset.agent_deployment_member
                            WHERE agent_deployment_id = %(deployment_id)s
                              AND member_role = 'primary' AND valid_to IS NULL
                            """,
                            {"deployment_id": expected_deployment_id},
                        ).fetchone()
                        if primary_key_row is None:
                            raise ConflictError("active deployment has no current primary member")
                        if not primary_key_row["runtime_agent_key"]:
                            raise ConflictError(
                                "source deployment has unresolved runtime keys; run deployment sync with the confirmed OpenClaw key before migrating"
                            )
                        if primary_key_row["runtime_agent_key"] != runtime_agent_key:
                            raise ConflictError("runtime_agent_key changed; reload the deployment before migrating")
                        target_definitions, target_retired = self._definitions(
                            connection, agent, target, parameters, rebase_workspace=True,
                        )
                        self._assert_no_foreign_active_members(
                            connection, agent["agent_id"], target_definitions,
                        )
                        source_parameters = {
                            "runtime_id": str(source["runtime_id"]),
                            "runtime_agent_key": runtime_agent_key,
                            **({"model": parameters["model"]} if "model" in parameters else {}),
                        }
                        source_apply, source_retired = self._definitions(
                            connection, agent, source, source_parameters,
                        )
                        source_cleanup = self._deployment_cleanup_definitions(
                            connection, expected_deployment_id,
                        )
                        self._assert_source_synchronized(
                            source_cleanup, source_apply, source_retired,
                        )
                        target_parameters = {
                            name: value for name, value in parameters.items()
                            if name in {"runtime_id", "runtime_agent_key", "workspace_root", "model"}
                        }
                        plan.update({
                            "expected_deployment_id": str(expected_deployment_id),
                            "expected_deployment_row_version": expected["row_version"],
                            "source_runtime": self._runtime_snapshot(source),
                            "target_runtime": self._runtime_snapshot(target),
                            "definitions": target_definitions,
                            "retired_definitions": target_retired,
                            "source_cleanup_definitions": source_cleanup,
                            "source_apply_definitions": source_apply,
                            "source_retired_definitions": source_retired,
                            "source_apply_parameters": source_parameters,
                            "target_apply_parameters": target_parameters,
                        })
                    locked_runtimes = [source["runtime_id"], target["runtime_id"]]
                    locked_servers = [source["server_id"], target["server_id"]]
                    host_runtime, host_server = target["runtime_id"], target["server_id"]

                self._lock_agent_runtimes(connection, locked_runtimes)
                OperationService._lock_target_servers(connection, locked_servers)
                plan["locked_runtime_ids"] = [str(value) for value in locked_runtimes]
                plan["locked_server_ids"] = [str(value) for value in locked_servers]

            run_id = uuid4()
            connection.execute(
                """
                INSERT INTO automation.operation_run (
                    run_id, operation_code, target_type, parameters, requested_by,
                    target_count, playbook_version, automation_version_id
                ) VALUES (%(run_id)s, %(operation_code)s, 'agent', %(parameters)s,
                          %(requested_by)s, 1, %(playbook_version)s, %(automation_version_id)s)
                """,
                {
                    "run_id": run_id,
                    "operation_code": operation_code,
                    "parameters": Jsonb(parameters),
                    "requested_by": requested_by,
                    "playbook_version": OperationService._playbook_version(operation),
                    "automation_version_id": operation.version_id,
                },
            )
            connection.execute(
                """
                INSERT INTO automation.operation_host_result (
                    run_id, server_id, runtime_id, agent_id, agent_deployment_id,
                    target_key, target_plan
                ) VALUES (%(run_id)s, %(server_id)s, %(runtime_id)s, %(agent_id)s,
                          %(deployment_id)s, %(target_key)s, %(plan)s)
                """,
                {
                    "run_id": run_id,
                    "server_id": host_server,
                    "runtime_id": host_runtime,
                    "agent_id": agent["agent_id"],
                    "deployment_id": deployment_id,
                    "target_key": str(agent["agent_id"]),
                    "plan": Jsonb(plan),
                },
            )
        return run_id

    @staticmethod
    def _build_channel_patch(connection, definitions, *, enabled=True):
        accounts = {}
        cipher = None
        for definition in definitions:
            if definition["channel_type"] != "feishu":
                continue
            configuration_id = definition.get("channel_configuration_id")
            if not configuration_id:
                raise ConflictError(f"{definition['name']} 的飞书渠道缺少配置记录")
            channel = connection.execute(
                """
                SELECT agent_channel_configuration_id, channel_type::text AS channel_type,
                       application_name, app_id, encrypted_app_secret, enabled, row_version
                FROM asset.agent_channel_configuration
                WHERE agent_channel_configuration_id = %(configuration_id)s
                """,
                {"configuration_id": UUID(configuration_id)},
            ).fetchone()
            if (
                channel is None
                or not channel["enabled"]
                or channel["channel_type"] != "feishu"
                or channel["row_version"] != definition["channel_row_version"]
                or channel["app_id"] != definition["channel_app_id"]
            ):
                raise ConflictError(f"{definition['name']} 的飞书渠道已变化，请重新发起部署同步")
            cipher = cipher or CredentialCipher()
            credential = cipher.decrypt(channel["encrypted_app_secret"])
            app_secret = credential.get("app_secret")
            if not app_secret:
                raise ConflictError(f"{definition['name']} 的飞书 AppSecret 无法读取")
            accounts[definition["channel_account_id"]] = {
                "enabled": enabled,
                "appId": channel["app_id"],
                "appSecret": app_secret,
                "name": channel["application_name"],
            }
        if not accounts:
            return None
        feishu_patch = {"accounts": accounts}
        if enabled:
            feishu_patch.update({
                "enabled": True,
                "connectionMode": "websocket",
            })
        return {
            "channels": {
                "feishu": feishu_patch,
            },
        }

    @staticmethod
    def _connection_target(
        row, definitions, retired_definitions, channel_patch_file=None,
        *, require_existing_agent_keys=None, allow_existing_agent_keys=None,
        require_owned_agent_keys=None, expected_channel_enabled=None,
        ownership_cleanup_mode=None,
    ):
        if row["connection_type"] is None:
            raise ValueError("宿主服务器未配置 Ansible 连接")
        if (
            row["connection_type"] == "ssh" and row.get("password_configured")
            and row.get("bootstrap_status") != "succeeded"
        ):
            raise ValueError("宿主服务器尚未完成密码建联和公钥复验")
        if row["connection_type"] == "local" and os.environ.get("AGENTSWATCH_ALLOW_LOCAL_CONNECTION") != "true":
            raise ValueError("本机连接未启用")
        target = {
            "server_id": str(row["agent_id"]),
            "address": row["host"],
            "connection": row["connection_type"],
            "port": row["port"],
            "user": row["username"],
            "become": row["use_become"],
            "runtime_family_code": row["family_code"],
            "runtime_config_path": row["config_path"],
            "runtime_state_path": row["state_path"],
            "runtime_management_driver": row["management_driver"],
            "runtime_management_target": row["management_target"],
            "runtime_endpoint_port": row["endpoint_port"],
            "runtime_probe_host": "127.0.0.1",
            "agentswatch_agent_definitions": definitions,
            "agentswatch_retired_agent_definitions": retired_definitions,
            "agentswatch_require_existing_agent_keys": require_existing_agent_keys or [],
            "agentswatch_allow_existing_agent_keys": allow_existing_agent_keys or [],
            "agentswatch_require_owned_agent_keys": require_owned_agent_keys or [],
        }
        if ownership_cleanup_mode is not None:
            target["agentswatch_ownership_cleanup_mode"] = ownership_cleanup_mode
        if expected_channel_enabled is not None:
            target["agentswatch_expected_channel_enabled"] = bool(expected_channel_enabled)
        if row.get("ssh_host_keys"):
            target["ssh_host_keys"] = list(row["ssh_host_keys"])
        if channel_patch_file:
            target["agentswatch_channel_patch_file"] = channel_patch_file
        credential_ref = row["credential_ref"]
        if credential_ref and credential_ref != "ssh-agent":
            key_file = OperationService._credential_files().get(credential_ref)
            if not key_file:
                raise ValueError(f"凭据引用未解析: {credential_ref}")
            target["private_key_file"] = key_file
        return target

    def _execution_runtime(self, runtime_id, agent_id):
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT runtime.runtime_id, runtime.server_id, runtime.row_version,
                       runtime.deployment_mode::text AS deployment_mode,
                       runtime.lifecycle_status::text AS lifecycle_status,
                       runtime.config_path, runtime.state_path, runtime.endpoint_port,
                       runtime.management_driver::text AS management_driver,
                       runtime.management_target, family.family_code,
                       server.lifecycle_status::text AS server_lifecycle_status,
                       connection.connection_type::text AS connection_type,
                       connection.host, connection.port, connection.username,
                       connection.credential_ref, connection.use_become,
                       connection.ssh_host_keys,
                       connection.bootstrap_status::text AS bootstrap_status,
                       connection.password_configured
                FROM asset.runtime_instance runtime
                JOIN asset.runtime_family family ON family.runtime_family_id = runtime.runtime_family_id
                JOIN asset.server_asset server ON server.server_id = runtime.server_id
                LEFT JOIN asset.server_connection connection ON connection.server_id = runtime.server_id
                WHERE runtime.runtime_id = %(runtime_id)s
                """,
                {"runtime_id": runtime_id},
            ).fetchone()
        if row is None:
            raise ConflictError("runtime disappeared after the deployment plan was queued")
        result = dict(row)
        result["agent_id"] = agent_id
        return result

    @staticmethod
    def _validate_remote_definition_contract(
        definitions, retired_definitions, *, require_existing_agent_keys=None,
        allow_existing_agent_keys=None, require_owned_agent_keys=None,
    ):
        desired_keys = [item.get("runtime_agent_key") for item in definitions]
        retired_keys = [item.get("runtime_agent_key") for item in retired_definitions]
        all_keys = desired_keys + retired_keys
        if any(
            not key or not isinstance(key, str) or not RUNTIME_AGENT_KEY.fullmatch(key)
            for key in all_keys
        ):
            raise ConflictError(
                "remote deployment definitions contain an invalid runtime agent key"
            )
        if len(all_keys) != len(set(all_keys)):
            raise ConflictError(
                "remote deployment definitions contain duplicate runtime agent keys"
            )
        desired = set(desired_keys)
        combined = set(all_keys)
        for label, values, allowed in (
            ("required existing", require_existing_agent_keys or [], desired),
            ("allowed existing", allow_existing_agent_keys or [], desired),
            ("required ownership", require_owned_agent_keys or [], combined),
        ):
            if not set(values).issubset(allowed):
                raise ConflictError(
                    f"{label} runtime agent keys are outside the remote definition set"
                )

    @classmethod
    def _validate_runtime_snapshot(cls, row, snapshot, *, label):
        cls._validate_runtime(row, label=label)
        current = cls._runtime_snapshot(row)
        if any(current.get(key) != snapshot.get(key) for key in snapshot):
            raise ConflictError(f"{label} runtime changed after the deployment plan was queued")

    def _run_remote(
        self, operation_code, row, definitions, retired_definitions, parameters,
        events, event_prefix, *, channel_enabled=None, require_existing_agent_keys=None,
        allow_existing_agent_keys=None, require_owned_agent_keys=None,
        ownership_cleanup_mode=None,
    ):
        channel_patch_path = None
        try:
            self._validate_remote_definition_contract(
                definitions, retired_definitions,
                require_existing_agent_keys=require_existing_agent_keys,
                allow_existing_agent_keys=allow_existing_agent_keys,
                require_owned_agent_keys=require_owned_agent_keys,
            )
            if channel_enabled is not None:
                with self.database.connection() as connection:
                    channel_patch = self._build_channel_patch(
                        connection, definitions, enabled=channel_enabled,
                    )
                if channel_patch:
                    file_descriptor, channel_patch_path = tempfile.mkstemp(
                        prefix="agentswatch-feishu-", suffix=".json",
                    )
                    os.chmod(channel_patch_path, 0o600)
                    with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                        json.dump(channel_patch, handle, ensure_ascii=False, separators=(",", ":"))
            target = self._connection_target(
                row, definitions, retired_definitions, channel_patch_path,
                require_existing_agent_keys=require_existing_agent_keys,
                allow_existing_agent_keys=allow_existing_agent_keys,
                require_owned_agent_keys=require_owned_agent_keys,
                expected_channel_enabled=channel_enabled,
                ownership_cleanup_mode=ownership_cleanup_mode,
            )
            events.system(
                f"{event_prefix}_started", f"开始执行受控步骤：{event_prefix}",
                phase="ansible", target_key=str(row["agent_id"]),
            )
            report = AutomationRunner(self.catalog).run(
                operation_code, [target], parameters,
                event_callback=events.record,
            )
            host = report["hosts"][str(row["agent_id"])]
            status, result, error = host["status"], host["result"], host["error"]
            if status == "succeeded" and result is None:
                status, error = "failed", "Playbook 未返回结构化结果"
            if status == "succeeded":
                events.system(
                    f"{event_prefix}_completed", f"受控步骤已完成：{event_prefix}",
                    phase="ansible", target_key=str(row["agent_id"]),
                )
            else:
                events.system(
                    f"{event_prefix}_failed", error or f"{event_prefix} failed",
                    phase="ansible", level="error", target_key=str(row["agent_id"]),
                )
            return {"status": status, "result": result, "error": error}
        except Exception as exc:
            events.system(
                f"{event_prefix}_failed", str(exc), phase="ansible", level="error",
                target_key=str(row["agent_id"]),
            )
            return {"status": "failed", "result": None, "error": str(exc)}
        finally:
            if channel_patch_path:
                Path(channel_patch_path).unlink(missing_ok=True)

    @staticmethod
    def _close_deployment(connection, deployment, requested_by, *, replacement_runtime_id=None):
        deployment_id = deployment["agent_deployment_id"]
        members = list(connection.execute(
            """
            SELECT agent_id, runtime_agent_key, config_ref
            FROM asset.agent_deployment_member
            WHERE agent_deployment_id = %(deployment_id)s AND valid_to IS NULL
            ORDER BY deployment_member_id
            """,
            {"deployment_id": deployment_id},
        ).fetchall())
        connection.execute(
            """
            UPDATE asset.agent_deployment_member
            SET valid_to = GREATEST(clock_timestamp(), valid_from + interval '1 microsecond')
            WHERE agent_deployment_id = %(deployment_id)s AND valid_to IS NULL
            """,
            {"deployment_id": deployment_id},
        )
        result = connection.execute(
            """
            UPDATE asset.agent_deployment
            SET lifecycle_status = 'inactive',
                valid_to = GREATEST(clock_timestamp(), valid_from + interval '1 microsecond')
            WHERE agent_deployment_id = %(deployment_id)s
              AND runtime_id = %(runtime_id)s
              AND lifecycle_status = 'active'
              AND row_version = %(row_version)s
            """,
            {
                "deployment_id": deployment_id,
                "runtime_id": deployment["runtime_id"],
                "row_version": deployment["row_version"],
            },
        )
        if result.rowcount != 1:
            raise ConflictError("deployment changed before the relationship could be finalized")
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value
            ) VALUES ('user', %(actor)s, 'ui', 'agent_deployment', %(deployment_id)s,
                      'unlink', ARRAY['lifecycle_status', 'members'], %(before)s, %(after)s)
            """,
            {
                "actor": requested_by,
                "deployment_id": deployment_id,
                "before": Jsonb({
                    "lifecycle_status": "active",
                    "runtime_id": str(deployment["runtime_id"]),
                    "members": [
                        {
                            "agent_id": str(item["agent_id"]),
                            "runtime_agent_key": item["runtime_agent_key"],
                            "config_ref": item["config_ref"],
                        }
                        for item in members
                    ],
                }),
                "after": Jsonb({
                    "lifecycle_status": "inactive",
                    "replacement_runtime_id": (
                        str(replacement_runtime_id) if replacement_runtime_id else None
                    ),
                    "agent_identity_retained": True,
                    "workspace_retained": True,
                }),
            },
        )

    def _persist_apply(self, row, plan, remote_result, requested_by):
        runtime_id = UUID(plan["target_runtime"]["runtime_id"])
        with self.database.connection() as connection:
            runtime = connection.execute(
                """
                SELECT runtime.runtime_id, runtime.server_id, runtime.row_version,
                       runtime.deployment_mode::text AS deployment_mode,
                       runtime.lifecycle_status::text AS lifecycle_status,
                       runtime.config_path, runtime.state_path, runtime.endpoint_port,
                       runtime.management_driver::text AS management_driver,
                       runtime.management_target, family.family_code,
                       server.lifecycle_status::text AS server_lifecycle_status
                FROM asset.runtime_instance runtime
                JOIN asset.runtime_family family
                  ON family.runtime_family_id = runtime.runtime_family_id
                JOIN asset.server_asset server ON server.server_id = runtime.server_id
                WHERE runtime.runtime_id = %(runtime_id)s
                FOR UPDATE OF runtime, server
                """,
                {"runtime_id": runtime_id},
            ).fetchone()
            self._validate_runtime_snapshot(
                runtime, plan["target_runtime"], label="target",
            )
            expected_row_version = plan.get("expected_deployment_row_version")
            if expected_row_version is not None:
                deployment = connection.execute(
                    """
                    SELECT agent_deployment_id, primary_agent_id, runtime_id,
                           lifecycle_status::text AS lifecycle_status, row_version
                    FROM asset.agent_deployment
                    WHERE agent_deployment_id = %(deployment_id)s
                    FOR UPDATE
                    """,
                    {"deployment_id": row["agent_deployment_id"]},
                ).fetchone()
                if (
                    deployment is None
                    or deployment["primary_agent_id"] != row["agent_id"]
                    or deployment["runtime_id"] != runtime_id
                    or deployment["lifecycle_status"] != "active"
                    or deployment["row_version"] != expected_row_version
                ):
                    raise ConflictError(
                        "active deployment changed before synchronization was finalized"
                    )
            else:
                active = connection.execute(
                    """
                    SELECT agent_deployment_id FROM asset.agent_deployment
                    WHERE primary_agent_id = %(agent_id)s
                      AND lifecycle_status = 'active'
                    FOR UPDATE
                    """,
                    {"agent_id": row["agent_id"]},
                ).fetchone()
                occupied = connection.execute(
                    """
                    SELECT agent_deployment_id FROM asset.agent_deployment
                    WHERE runtime_id = %(runtime_id)s
                      AND lifecycle_status = 'active'
                    FOR UPDATE
                    """,
                    {"runtime_id": runtime_id},
                ).fetchone()
                if active or occupied:
                    raise ConflictError(
                        "deployment relationship changed before first apply was finalized"
                    )
            return self._persist_deployment(
                connection, row, plan["definitions"], remote_result, requested_by,
            )

    def _persist_removal(self, row, plan, requested_by):
        with self.database.connection() as connection:
            source_runtime = connection.execute(
                """
                SELECT runtime.runtime_id, runtime.server_id, runtime.row_version,
                       runtime.deployment_mode::text AS deployment_mode,
                       runtime.lifecycle_status::text AS lifecycle_status,
                       runtime.config_path, runtime.state_path, runtime.endpoint_port,
                       runtime.management_driver::text AS management_driver,
                       runtime.management_target, family.family_code,
                       server.lifecycle_status::text AS server_lifecycle_status
                FROM asset.runtime_instance runtime
                JOIN asset.runtime_family family ON family.runtime_family_id = runtime.runtime_family_id
                JOIN asset.server_asset server ON server.server_id = runtime.server_id
                WHERE runtime.runtime_id = %(runtime_id)s
                FOR UPDATE OF runtime, server
                """,
                {"runtime_id": UUID(plan["source_runtime"]["runtime_id"])},
            ).fetchone()
            if source_runtime is None:
                raise ConflictError("source runtime disappeared before removal was finalized")
            if plan["mode"] == "remove_unmanaged":
                current_snapshot = self._runtime_snapshot(source_runtime)
                if any(
                    current_snapshot.get(key) != value
                    for key, value in plan["source_runtime"].items()
                ):
                    raise ConflictError(
                        "source runtime changed after the metadata-only removal was queued"
                    )
            else:
                self._validate_runtime_snapshot(
                    source_runtime, plan["source_runtime"], label="source",
                )
            deployment = connection.execute(
                """
                SELECT agent_deployment_id, primary_agent_id, runtime_id,
                       lifecycle_status::text AS lifecycle_status, row_version
                FROM asset.agent_deployment
                WHERE agent_deployment_id = %(deployment_id)s
                FOR UPDATE
                """,
                {"deployment_id": UUID(plan["expected_deployment_id"])},
            ).fetchone()
            if (
                deployment is None
                or deployment["primary_agent_id"] != row["agent_id"]
                or deployment["lifecycle_status"] != "active"
                or deployment["runtime_id"] != UUID(plan["source_runtime"]["runtime_id"])
                or deployment["row_version"] != plan["expected_deployment_row_version"]
            ):
                raise ConflictError("active deployment changed before removal was finalized")
            self._close_deployment(connection, deployment, requested_by)
        return deployment["agent_deployment_id"]

    def _persist_migration(self, row, plan, remote_result, requested_by):
        source_runtime_id = UUID(plan["source_runtime"]["runtime_id"])
        target_runtime_id = UUID(plan["target_runtime"]["runtime_id"])
        with self.database.connection() as connection:
            locked_runtimes = {}
            for runtime_id in sorted([source_runtime_id, target_runtime_id], key=str):
                locked_runtimes[runtime_id] = connection.execute(
                    """
                    SELECT runtime.runtime_id, runtime.server_id, runtime.row_version,
                           runtime.deployment_mode::text AS deployment_mode,
                           runtime.lifecycle_status::text AS lifecycle_status,
                           runtime.config_path, runtime.state_path, runtime.endpoint_port,
                           runtime.management_driver::text AS management_driver,
                           runtime.management_target, family.family_code,
                           server.lifecycle_status::text AS server_lifecycle_status
                    FROM asset.runtime_instance runtime
                    JOIN asset.runtime_family family ON family.runtime_family_id = runtime.runtime_family_id
                    JOIN asset.server_asset server ON server.server_id = runtime.server_id
                    WHERE runtime.runtime_id = %(runtime_id)s
                    FOR UPDATE OF runtime, server
                    """,
                    {"runtime_id": runtime_id},
                ).fetchone()
            self._validate_runtime_snapshot(
                locked_runtimes[source_runtime_id], plan["source_runtime"], label="source",
            )
            self._validate_runtime_snapshot(
                locked_runtimes[target_runtime_id], plan["target_runtime"], label="target",
            )
            deployment = connection.execute(
                """
                SELECT agent_deployment_id, primary_agent_id, runtime_id,
                       lifecycle_status::text AS lifecycle_status, row_version
                FROM asset.agent_deployment
                WHERE agent_deployment_id = %(deployment_id)s
                FOR UPDATE
                """,
                {"deployment_id": UUID(plan["expected_deployment_id"])},
            ).fetchone()
            if (
                deployment is None
                or deployment["primary_agent_id"] != row["agent_id"]
                or deployment["lifecycle_status"] != "active"
                or deployment["runtime_id"] != source_runtime_id
                or deployment["row_version"] != plan["expected_deployment_row_version"]
            ):
                raise ConflictError("active source deployment changed before migration was finalized")
            occupied = connection.execute(
                """
                SELECT agent_deployment_id FROM asset.agent_deployment
                WHERE runtime_id = %(runtime_id)s AND lifecycle_status = 'active'
                """,
                {"runtime_id": target_runtime_id},
            ).fetchone()
            if occupied:
                raise ConflictError("target runtime became occupied before migration was finalized")
            self._close_deployment(
                connection, deployment, requested_by, replacement_runtime_id=target_runtime_id,
            )
            target_row = {
                **row,
                "runtime_id": target_runtime_id,
                "agent_deployment_id": None,
            }
            return self._persist_deployment(
                connection, target_row, plan["definitions"], remote_result, requested_by,
            )

    @staticmethod
    def _persist_deployment(connection, row, definitions, result, requested_by):
        reported_workspaces = result.get("agent_workspaces") if isinstance(result, dict) else None
        if not isinstance(reported_workspaces, dict):
            raise ConflictError("OpenClaw deployment result omitted the observed agent workspace map")
        actual_workspaces = {}
        for definition in definitions:
            runtime_key = definition["runtime_agent_key"]
            workspace_path = str(reported_workspaces.get(runtime_key) or "").strip()
            if not workspace_path or not PurePosixPath(workspace_path).is_absolute():
                raise ConflictError(
                    f"OpenClaw did not report an absolute workspace for agent {runtime_key}"
                )
            actual_workspaces[runtime_key] = workspace_path

        deployment_id = row["agent_deployment_id"]
        changed = False
        if deployment_id is None:
            deployment_id = uuid4()
            changed = True
            connection.execute(
                """
                INSERT INTO asset.agent_deployment (
                    agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
                ) VALUES (%(deployment_id)s, %(agent_id)s, %(runtime_id)s, 'active')
                """,
                {"deployment_id": deployment_id, "agent_id": row["agent_id"], "runtime_id": row["runtime_id"]},
            )
        current = {
            item["agent_id"]: item
            for item in connection.execute(
                """
                SELECT deployment_member_id, agent_id, runtime_agent_key,
                       mapping_status::text AS mapping_status, config_ref, row_version
                FROM asset.agent_deployment_member
                WHERE agent_deployment_id = %(deployment_id)s AND valid_to IS NULL
                """,
                {"deployment_id": deployment_id},
            ).fetchall()
        }
        primary_member_id = next(
            (item["deployment_member_id"] for item in current.values() if item["agent_id"] == row["agent_id"]),
            None,
        )
        for definition in definitions:
            agent_id = UUID(definition["agent_id"])
            actual_workspace = actual_workspaces[definition["runtime_agent_key"]]
            if agent_id in current:
                member = current[agent_id]
                if member["mapping_status"] == "verified":
                    if member["runtime_agent_key"] == definition["runtime_agent_key"]:
                        if member["config_ref"] == actual_workspace:
                            continue
                        changed = True
                        updated = connection.execute(
                            """
                            UPDATE asset.agent_deployment_member
                            SET config_ref = %(config_ref)s
                            WHERE deployment_member_id = %(member_id)s
                              AND mapping_status = 'verified'
                              AND runtime_agent_key = %(runtime_key)s
                              AND row_version = %(row_version)s
                              AND valid_to IS NULL
                            """,
                            {
                                "config_ref": actual_workspace,
                                "member_id": member["deployment_member_id"],
                                "runtime_key": definition["runtime_agent_key"],
                                "row_version": member["row_version"],
                            },
                        )
                        if updated.rowcount != 1:
                            raise ConflictError(
                                "verified deployment member changed before its workspace could be synchronized"
                            )
                        continue
                    raise ConflictError("verified runtime_agent_key cannot be changed by deployment sync")
                changed = True
                updated = connection.execute(
                    """
                    UPDATE asset.agent_deployment_member
                    SET runtime_agent_key = %(runtime_key)s,
                        mapping_status = 'verified', config_ref = %(config_ref)s
                    WHERE deployment_member_id = %(member_id)s
                      AND mapping_status <> 'verified'
                      AND runtime_agent_key IS NOT DISTINCT FROM %(expected_runtime_key)s
                      AND row_version = %(row_version)s
                      AND valid_to IS NULL
                    """,
                    {
                        "runtime_key": definition["runtime_agent_key"],
                        "expected_runtime_key": member["runtime_agent_key"],
                        "config_ref": actual_workspace,
                        "member_id": member["deployment_member_id"],
                        "row_version": member["row_version"],
                    },
                )
                if updated.rowcount != 1:
                    raise ConflictError(
                        "unresolved deployment member changed before it could be verified"
                    )
                continue
            changed = True
            member_id = uuid4()
            if definition["structure_role"] == "primary":
                primary_member_id = member_id
            connection.execute(
                """
                INSERT INTO asset.agent_deployment_member (
                    deployment_member_id, agent_deployment_id, runtime_id, agent_id,
                    member_role, parent_member_id, runtime_agent_key, mapping_status, config_ref
                ) VALUES (%(member_id)s, %(deployment_id)s, %(runtime_id)s, %(agent_id)s,
                          %(role)s, %(parent_id)s, %(runtime_key)s, 'verified', %(config_ref)s)
                """,
                {
                    "member_id": member_id,
                    "deployment_id": deployment_id,
                    "runtime_id": row["runtime_id"],
                    "agent_id": agent_id,
                    "role": definition["structure_role"],
                    "parent_id": None if definition["structure_role"] == "primary" else primary_member_id,
                    "runtime_key": definition["runtime_agent_key"],
                    "config_ref": actual_workspace,
                },
            )
        desired_agent_ids = {UUID(definition["agent_id"]) for definition in definitions}
        obsolete_agent_ids = list(set(current) - desired_agent_ids)
        if obsolete_agent_ids:
            changed = True
            connection.execute(
                """
                UPDATE asset.agent_deployment_member
                SET valid_to = GREATEST(clock_timestamp(), valid_from + interval '1 microsecond')
                WHERE agent_deployment_id = %(deployment_id)s
                  AND agent_id = ANY(%(agent_ids)s)
                  AND valid_to IS NULL
                """,
                {"deployment_id": deployment_id, "agent_ids": obsolete_agent_ids},
            )
        if changed:
            connection.execute(
                """
                INSERT INTO audit.change_log (
                    actor_type, actor_ref, source, object_type, object_id,
                    operation, changed_fields, after_value
                ) VALUES ('user', %(actor)s, 'ui', 'agent_deployment', %(deployment_id)s,
                          'link', ARRAY['runtime_id', 'members'], %(after)s)
                """,
                {
                    "actor": requested_by,
                    "deployment_id": deployment_id,
                    "after": Jsonb({
                        "primary_agent_id": str(row["agent_id"]),
                        "runtime_id": str(row["runtime_id"]),
                        "deployed_agent_keys": result.get("deployed_agent_keys", []),
                        "retired_agent_keys": result.get("retired_agent_keys", []),
                        "agent_workspaces": actual_workspaces,
                    }),
                },
            )
        return deployment_id

    def execute_run(self, run_id):
        try:
            return self._execute_run(run_id)
        except Exception as exc:
            finalize_unhandled_run(run_id, exc, self.database)

    def _terminal_replay_plan(self, row, operation_code):
        plan = dict(row["plan"])
        with self.database.connection() as connection:
            active = self._active_deployment(connection, row["agent_id"])
            if operation_code == "agent.apply_deployment":
                if row["agent_deployment_id"] is not None:
                    if (
                        active is None
                        or active["agent_deployment_id"] != row["agent_deployment_id"]
                        or active["runtime_id"] != row["runtime_id"]
                        or active["row_version"]
                        != plan.get("expected_deployment_row_version")
                    ):
                        raise ConflictError(
                            "active deployment drifted before synchronization replay"
                        )
                    plan["mode"] = "apply_deployment"
                    return plan
                if active is None:
                    plan["mode"] = "apply_deployment"
                    return plan
                if (
                    active["runtime_id"] != row["runtime_id"]
                    or not self._deployment_matches_definitions(
                        connection, active["agent_deployment_id"],
                        plan.get("definitions", []),
                    )
                ):
                    raise ConflictError(
                        "a different active deployment exists after first-apply replay"
                    )
                plan.update({
                    "mode": "already_applied",
                    "active_deployment_id": str(active["agent_deployment_id"]),
                })
                return plan
            expected = connection.execute(
                """
                SELECT lifecycle_status::text AS lifecycle_status, runtime_id, row_version
                FROM asset.agent_deployment
                WHERE agent_deployment_id = %(deployment_id)s
                  AND primary_agent_id = %(agent_id)s
                """,
                {
                    "deployment_id": UUID(plan["expected_deployment_id"]),
                    "agent_id": row["agent_id"],
                },
            ).fetchone()
            if expected is None:
                raise ConflictError("expected deployment disappeared during replay")
            if expected["lifecycle_status"] == "active":
                if (
                    active is None
                    or active["agent_deployment_id"]
                    != UUID(plan["expected_deployment_id"])
                    or expected["row_version"]
                    != plan.get("expected_deployment_row_version")
                ):
                    raise ConflictError(
                        "active source deployment drifted during replay"
                    )
                if operation_code == "agent.migrate_deployment":
                    plan["mode"] = "migrate_deployment"
                elif plan.get("mode") == "already_removed":
                    raise ConflictError(
                        "removal replay plan has no executable active-source snapshot"
                    )
                return plan
            if operation_code == "agent.remove_deployment":
                if (
                    active is not None
                    or self._deployment_member_state(
                        connection, UUID(plan["expected_deployment_id"]),
                    )
                ):
                    raise ConflictError(
                        "removed deployment terminal state has relationship drift"
                    )
                plan["unmanaged_mapping"] = plan.get("mode") == "remove_unmanaged"
                plan["mode"] = "already_removed"
                return plan
            if (
                active is None
                or active["runtime_id"] != UUID(plan["target_runtime"]["runtime_id"])
                or not self._deployment_matches_definitions(
                    connection, active["agent_deployment_id"],
                    plan.get("definitions", []),
                )
            ):
                raise ConflictError(
                    "migrated deployment terminal state no longer matches the queued request"
                )
            plan.update({
                "mode": "already_migrated",
                "active_deployment_id": str(active["agent_deployment_id"]),
            })
        return plan

    def _compensate_apply_persistence(
        self, target, plan, events, *, cleanup_mode="required",
    ):
        compensation_plan = plan.get("apply_compensation") or {
            "mode": "cleanup_unconfirmed",
        }
        events.system(
            "compensation_started",
            "远端同步已完成但平台关系未能确认，正在恢复远端安全状态。",
            phase="ansible", level="warning", target_key=str(target["agent_id"]),
        )
        if compensation_plan["mode"] == "restore_existing":
            restored = self._run_remote(
                "agent.apply_deployment", target,
                compensation_plan["source_apply_definitions"],
                compensation_plan["source_retired_definitions"],
                compensation_plan["source_apply_parameters"],
                events, "source_restore", channel_enabled=True,
                allow_existing_agent_keys=[
                    item["runtime_agent_key"]
                    for item in compensation_plan["source_apply_definitions"]
                ],
                require_owned_agent_keys=[
                    item["runtime_agent_key"]
                    for item in (
                        compensation_plan["source_apply_definitions"]
                        + compensation_plan["source_retired_definitions"]
                    )
                ],
            )
            source_restored = restored["status"] == "succeeded"
            target_cleaned = True
            detail = {"source_restore": restored}
        else:
            cleaned = self._run_remote(
                "agent.remove_deployment", target, [], plan["definitions"],
                {"expected_deployment_id": plan["cleanup_reference_id"]},
                events, "target_cleanup",
                ownership_cleanup_mode=cleanup_mode,
            )
            source_restored = True
            target_cleaned = cleaned["status"] == "succeeded"
            detail = {"target_cleanup": cleaned}
        recovery_required = not (source_restored and target_cleaned)
        events.system(
            "compensation_failed" if recovery_required else "compensation_completed",
            (
                "远端自动补偿未完整完成，需要人工恢复。"
                if recovery_required else "远端已恢复到未确认关系之前的安全状态。"
            ),
            phase="ansible", level="error" if recovery_required else "warning",
            target_key=str(target["agent_id"]),
        )
        return {
            "source_restored": source_restored,
            "target_cleaned": target_cleaned,
            "recovery_required": recovery_required,
            **detail,
        }

    def _compensate_migration(self, source, target, plan, events, *, restore_source):
        events.system(
            "compensation_started", "迁移未完成，开始恢复源端并清理目标端。",
            phase="ansible", level="warning", target_key=str(source["agent_id"]),
        )
        if restore_source:
            restored = self._run_remote(
                "agent.apply_deployment", source,
                plan["source_apply_definitions"], plan["source_retired_definitions"],
                plan["source_apply_parameters"], events, "source_restore",
                channel_enabled=True,
                allow_existing_agent_keys=[
                    item["runtime_agent_key"]
                    for item in plan["source_apply_definitions"]
                ],
                require_owned_agent_keys=[
                    item["runtime_agent_key"]
                    for item in (
                        plan["source_apply_definitions"]
                        + plan["source_retired_definitions"]
                    )
                ],
            )
            source_restored = restored["status"] == "succeeded"
        else:
            restored = {"status": "not_required", "result": None, "error": None}
            source_restored = True
        cleaned = self._run_remote(
            "agent.remove_deployment", target, [], plan["definitions"],
            {"expected_deployment_id": plan["expected_deployment_id"]},
            events, "target_cleanup",
            ownership_cleanup_mode=(
                "required" if restore_source else "compensation"
            ),
        )
        target_cleaned = cleaned["status"] == "succeeded"
        recovery_required = not (source_restored and target_cleaned)
        events.system(
            "compensation_failed" if recovery_required else "compensation_completed",
            (
                "自动补偿未完整完成，需要人工恢复。"
                if recovery_required else "自动补偿已完成，平台关系仍保持在源运行时。"
            ),
            phase="ansible", level="error" if recovery_required else "warning",
            target_key=str(source["agent_id"]),
        )
        return {
            "source_restored": source_restored,
            "target_cleaned": target_cleaned,
            "recovery_required": recovery_required,
            "source_restore": restored,
            "target_cleanup": cleaned,
        }

    def _compensate_removal(self, source, plan, events):
        events.system(
            "compensation_started", "解除未完成，开始恢复源端渠道配置。",
            phase="ansible", level="warning", target_key=str(source["agent_id"]),
        )
        restored = self._run_remote(
            "agent.apply_deployment", source,
            plan["source_apply_definitions"], plan["source_retired_definitions"],
            plan["source_apply_parameters"], events, "source_restore",
            channel_enabled=True,
            allow_existing_agent_keys=[
                item["runtime_agent_key"]
                for item in plan["source_apply_definitions"]
            ],
            require_owned_agent_keys=[
                item["runtime_agent_key"]
                for item in (
                    plan["source_apply_definitions"]
                    + plan["source_retired_definitions"]
                )
            ],
        )
        source_restored = restored["status"] == "succeeded"
        events.system(
            "compensation_completed" if source_restored else "compensation_failed",
            "源端渠道已恢复。" if source_restored else "源端渠道恢复失败，需要人工恢复。",
            phase="ansible", level="warning" if source_restored else "error",
            target_key=str(source["agent_id"]),
        )
        return {
            "source_restored": source_restored,
            "target_cleaned": True,
            "recovery_required": not source_restored,
            "source_restore": restored,
        }

    def _execute_run(self, run_id):
        with self.database.connection() as connection:
            run = connection.execute(
                """
                SELECT operation_code, parameters, requested_by, status::text AS status
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
            row = connection.execute(
                """
                SELECT host.*, host.target_plan AS plan
                FROM automation.operation_host_result host
                WHERE host.run_id = %(run_id)s
                """,
                {"run_id": run_id},
            ).fetchone()
            connection.execute(
                """
                UPDATE automation.operation_host_result
                SET status = 'running', started_at = now()
                WHERE run_id = %(run_id)s AND agent_id = %(agent_id)s
                """,
                {"run_id": run_id, "agent_id": row["agent_id"]},
            )
        record_system_event(
            run_id, "task_started", "任务已开始执行，正在准备智能体部署关系变更。",
            phase="preflight", database=self.database,
        )
        plan = self._terminal_replay_plan(row, run["operation_code"])
        result = {
            "operation_code": run["operation_code"],
            "relationship_switched": False,
            "source_restored": True,
            "target_cleaned": True,
            "recovery_required": False,
        }
        error = None
        status = "failed"
        deployment_id = row["agent_deployment_id"]
        with OperationEventRecorder(run_id, self.database) as events:
            try:
                if plan["mode"] == "already_removed":
                    status = "succeeded"
                    result.update({
                        "relationship_removed": True,
                        "idempotent_replay": True,
                        "retained_agent_identity": True,
                        "retained_workspaces": True,
                        "recovered_terminal_state": True,
                        "remote_cleanup_skipped": bool(plan.get("unmanaged_mapping")),
                        "remote_cleanup_not_required": bool(plan.get("unmanaged_mapping")),
                        "unmanaged_mapping": bool(plan.get("unmanaged_mapping")),
                    })
                elif plan["mode"] == "already_migrated":
                    status = "succeeded"
                    deployment_id = UUID(plan["active_deployment_id"])
                    result.update({
                        "relationship_switched": True,
                        "idempotent_replay": True,
                        "recovered_terminal_state": True,
                        "agent_deployment_id": plan["active_deployment_id"],
                    })
                elif plan["mode"] == "already_applied":
                    status = "succeeded"
                    deployment_id = UUID(plan["active_deployment_id"])
                    result.update({
                        "relationship_switched": False,
                        "idempotent_replay": True,
                        "recovered_terminal_state": True,
                        "agent_deployment_id": plan["active_deployment_id"],
                    })
                elif run["operation_code"] == "agent.apply_deployment":
                    target = self._execution_runtime(row["runtime_id"], row["agent_id"])
                    self._validate_runtime_snapshot(
                        target, plan["target_runtime"], label="target",
                    )
                    applied = self._run_remote(
                        "agent.apply_deployment", target,
                        plan["definitions"], plan.get("retired_definitions", []),
                        run["parameters"], events, "deployment_apply", channel_enabled=True,
                        require_existing_agent_keys=plan.get("require_existing_agent_keys", []),
                        allow_existing_agent_keys=plan.get("allow_existing_agent_keys", []),
                    )
                    result["deployment_apply"] = applied
                    status, error = applied["status"], applied["error"]
                    if status != "succeeded":
                        compensation = self._compensate_apply_persistence(
                            target, plan, events, cleanup_mode="compensation",
                        )
                        result.update(compensation)
                        error = (
                            f"远端同步失败: {applied['error']}; "
                            + (
                                "自动补偿已完成"
                                if not compensation["recovery_required"]
                                else "自动补偿不完整，需人工恢复"
                            )
                        )
                    else:
                        try:
                            deployment_id = self._persist_apply(
                                row, plan, applied["result"], run["requested_by"],
                            )
                        except Exception as exc:
                            compensation = self._compensate_apply_persistence(
                                target, plan, events,
                            )
                            result.update(compensation)
                            status = "failed"
                            error = (
                                f"远端同步完成但平台关系持久化失败: {exc}; "
                                + (
                                    "自动补偿已完成"
                                    if not compensation["recovery_required"]
                                    else "自动补偿不完整，需人工恢复"
                                )
                            )
                        else:
                            result.update(applied["result"] or {})
                            result["agent_deployment_id"] = str(deployment_id)
                elif plan["mode"] == "remove_unmanaged":
                    events.system(
                        "relationship_switch_started",
                        "该导入关系从未解析远端智能体键，正在仅解除平台元数据关系。",
                        phase="finalize", target_key=str(row["agent_id"]),
                    )
                    deployment_id = self._persist_removal(
                        row, plan, run["requested_by"],
                    )
                    events.system(
                        "relationship_switch_completed",
                        "未解析的导入关系已解除，无需执行远端清理。",
                        phase="finalize", target_key=str(row["agent_id"]),
                    )
                    status = "succeeded"
                    result.update({
                        "relationship_removed": True,
                        "source_restored": False,
                        "remote_cleanup_skipped": True,
                        "remote_cleanup_not_required": True,
                        "unmanaged_mapping": True,
                        "retained_agent_identity": True,
                        "retained_workspaces": True,
                    })
                elif run["operation_code"] == "agent.remove_deployment":
                    source = self._execution_runtime(
                        UUID(plan["source_runtime"]["runtime_id"]), row["agent_id"],
                    )
                    self._validate_runtime_snapshot(
                        source, plan["source_runtime"], label="source",
                    )
                    removed = self._run_remote(
                        "agent.remove_deployment", source, [],
                        plan["source_cleanup_definitions"], run["parameters"],
                        events, "source_deactivate",
                        ownership_cleanup_mode="required",
                    )
                    result["source_deactivate"] = removed
                    if removed["status"] != "succeeded":
                        compensation = self._compensate_removal(source, plan, events)
                        result.update(compensation)
                        error = (
                            f"源端渠道清理失败，平台关系未变更: {removed['error']}; "
                            + ("已自动恢复源端" if compensation["source_restored"] else "源端自动恢复失败，需人工恢复")
                        )
                    else:
                        events.system(
                            "relationship_switch_started", "远端清理已验证，正在结束平台部署关系。",
                            phase="finalize", target_key=str(row["agent_id"]),
                        )
                        try:
                            deployment_id = self._persist_removal(row, plan, run["requested_by"])
                        except Exception as exc:
                            compensation = self._compensate_removal(source, plan, events)
                            result.update(compensation)
                            error = (
                                f"远端清理完成但关系 CAS 失败: {exc}; "
                                + ("已自动恢复源端" if compensation["source_restored"] else "源端自动恢复失败，需人工恢复")
                            )
                        else:
                            events.system(
                                "relationship_switch_completed", "平台部署关系已结束。",
                                phase="finalize", target_key=str(row["agent_id"]),
                            )
                            status = "succeeded"
                            result.update({
                                "relationship_removed": True,
                                "source_restored": False,
                                "retained_agent_identity": True,
                                "retained_workspaces": True,
                                **(removed["result"] or {}),
                            })
                else:
                    source = self._execution_runtime(
                        UUID(plan["source_runtime"]["runtime_id"]), row["agent_id"],
                    )
                    target = self._execution_runtime(
                        UUID(plan["target_runtime"]["runtime_id"]), row["agent_id"],
                    )
                    self._validate_runtime_snapshot(
                        source, plan["source_runtime"], label="source",
                    )
                    self._validate_runtime_snapshot(
                        target, plan["target_runtime"], label="target",
                    )
                    staged = self._run_remote(
                        "agent.apply_deployment", target, plan["definitions"],
                        plan.get("retired_definitions", []), plan["target_apply_parameters"],
                        events, "target_stage", channel_enabled=False,
                    )
                    result["target_stage"] = staged
                    if staged["status"] != "succeeded":
                        compensation = self._compensate_migration(
                            source, target, plan, events, restore_source=False,
                        )
                        result.update(compensation)
                        error = (
                            f"目标端预部署失败，源关系未变更: {staged['error']}; "
                            + ("目标端已清理" if compensation["target_cleaned"] else "目标端清理失败，需人工恢复")
                        )
                    else:
                        deactivated = self._run_remote(
                            "agent.remove_deployment", source, [],
                            plan["source_cleanup_definitions"],
                            {"expected_deployment_id": plan["expected_deployment_id"]},
                            events, "source_deactivate",
                            ownership_cleanup_mode="required",
                        )
                        result["source_deactivate"] = deactivated
                        if deactivated["status"] != "succeeded":
                            compensation = self._compensate_migration(
                                source, target, plan, events, restore_source=True,
                            )
                            result.update(compensation)
                            error = (
                                f"源端停用失败，平台关系未变更: {deactivated['error']}; "
                                + ("自动补偿完成" if not compensation["recovery_required"] else "自动补偿不完整，需人工恢复")
                            )
                        else:
                            activated = self._run_remote(
                                "agent.apply_deployment", target, plan["definitions"],
                                plan.get("retired_definitions", []), plan["target_apply_parameters"],
                                events, "target_activate", channel_enabled=True,
                            )
                            result["target_activate"] = activated
                            if activated["status"] != "succeeded":
                                compensation = self._compensate_migration(
                                    source, target, plan, events, restore_source=True,
                                )
                                result.update(compensation)
                                error = (
                                    f"目标端激活失败，平台关系未变更: {activated['error']}; "
                                    + ("自动补偿完成" if not compensation["recovery_required"] else "自动补偿不完整，需人工恢复")
                                )
                            else:
                                events.system(
                                    "relationship_switch_started",
                                    "目标端已激活且源端已停用，正在原子切换平台关系。",
                                    phase="finalize", target_key=str(row["agent_id"]),
                                )
                                try:
                                    deployment_id = self._persist_migration(
                                        row, plan, activated["result"] or {}, run["requested_by"],
                                    )
                                except Exception as exc:
                                    compensation = self._compensate_migration(
                                        source, target, plan, events, restore_source=True,
                                    )
                                    result.update(compensation)
                                    error = (
                                        f"远端切换完成但关系 CAS 失败: {exc}; "
                                        + ("自动补偿完成" if not compensation["recovery_required"] else "自动补偿不完整，需人工恢复")
                                    )
                                else:
                                    events.system(
                                        "relationship_switch_completed",
                                        "平台部署关系已原子切换到目标运行时。",
                                        phase="finalize", target_key=str(row["agent_id"]),
                                    )
                                    status = "succeeded"
                                    result.update({
                                        "relationship_switched": True,
                                        "source_restored": False,
                                        "target_cleaned": False,
                                        "agent_deployment_id": str(deployment_id),
                                        "previous_deployment_id": plan["expected_deployment_id"],
                                        "source_runtime_id": plan["source_runtime"]["runtime_id"],
                                        "target_runtime_id": plan["target_runtime"]["runtime_id"],
                                        "retained_agent_identity": True,
                                        "retained_source_workspaces": True,
                                        **(activated["result"] or {}),
                                    })
            except Exception as exc:
                error = str(exc)
                events.system(
                    "ansible_error", error, phase="ansible", level="error",
                    target_key=str(row["agent_id"]),
                )

        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE automation.operation_host_result
                SET status = %(status)s, result = %(result)s, error_message = %(error)s,
                    agent_deployment_id = %(deployment_id)s,
                    started_at = COALESCE(started_at, now()), finished_at = now()
                WHERE run_id = %(run_id)s AND agent_id = %(agent_id)s
                """,
                {
                    "status": status,
                    "result": Jsonb(result) if result is not None else None,
                    "error": error,
                    "deployment_id": deployment_id,
                    "run_id": run_id,
                    "agent_id": row["agent_id"],
                },
            )
            succeeded = 1 if status == "succeeded" else 0
            connection.execute(
                """
                UPDATE automation.operation_run
                SET status = %(status)s, succeeded_count = %(succeeded)s,
                    failed_count = %(failed)s, finished_at = now()
                WHERE run_id = %(run_id)s
                """,
                {
                    "status": "succeeded" if succeeded else "failed",
                    "succeeded": succeeded,
                    "failed": 1 - succeeded,
                    "run_id": run_id,
                },
            )
        record_system_event(
            run_id, "task_completed",
            "智能体部署关系任务执行成功。" if succeeded else f"智能体部署关系任务执行失败：{error}",
            phase="finalize", level="info" if succeeded else "error",
            data={"status": "succeeded" if succeeded else "failed", "succeeded": succeeded, "failed": 1 - succeeded},
            database=self.database,
        )
