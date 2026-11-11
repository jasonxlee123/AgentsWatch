import json
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .database import WriteDatabase
from .runtime_web_access import CredentialCipher
from .server_management import ConflictError, NotFoundError


AGENT_FIELDS = {
    "name", "service_archetype", "lifecycle_status", "owning_department_id",
    "business_admin_person_id", "it_admin_person_id", "primary_service_person_id",
    "primary_service_department_id", "business_domain", "business_scenario", "description",
}

STATUS_TRANSITIONS = {
    "requested": {"requested", "testing", "retired"},
    "testing": {"testing", "active", "suspended", "retired"},
    "active": {"active", "suspended", "retired"},
    "suspended": {"suspended", "active", "retired"},
    "retired": {"retired"},
}


class AgentManagementService:
    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _agent_row(connection, agent_id):
        return connection.execute(
            "SELECT * FROM asset.agent_asset WHERE agent_id = %(agent_id)s",
            {"agent_id": agent_id},
        ).fetchone()

    @staticmethod
    def _channel_row(connection, agent_id):
        return connection.execute(
            """
            SELECT * FROM asset.agent_channel_configuration
            WHERE agent_id = %(agent_id)s
            """,
            {"agent_id": agent_id},
        ).fetchone()

    @staticmethod
    def _active_deployment_operation(connection, primary_agent_id):
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
            {"lock_key": f"agent-operation:{primary_agent_id}"},
        )
        return connection.execute(
            """
            SELECT run.run_id
            FROM automation.operation_host_result host
            JOIN automation.operation_run run ON run.run_id = host.run_id
            WHERE host.agent_id = %(agent_id)s
              AND run.status IN ('queued', 'running')
            ORDER BY run.created_at DESC LIMIT 1
            """,
            {"agent_id": primary_agent_id},
        ).fetchone()

    @staticmethod
    def _sanitize_channel(row):
        if row is None or not row["enabled"]:
            return {"channel_type": "none"}
        return {
            "channel_type": row["channel_type"],
            "application_name": row["application_name"],
            "app_id": row["app_id"],
            "credential_configured": bool(row["encrypted_app_secret"]),
            "enabled": row["enabled"],
            "row_version": row["row_version"],
        }

    @classmethod
    def _audit_value(cls, connection, agent_id):
        row = cls._agent_row(connection, agent_id)
        if row is None:
            return None
        value = dict(row)
        value["accessible_person_ids"] = [
            str(item["person_id"])
            for item in connection.execute(
                "SELECT person_id FROM asset.agent_accessible_person "
                "WHERE agent_id = %(agent_id)s AND valid_to IS NULL ORDER BY person_id",
                {"agent_id": agent_id},
            ).fetchall()
        ]
        value["group_ids"] = [
            str(item["group_id"])
            for item in connection.execute(
                "SELECT group_id FROM asset.agent_group_member "
                "WHERE agent_id = %(agent_id)s AND valid_to IS NULL ORDER BY group_id",
                {"agent_id": agent_id},
            ).fetchall()
        ]
        value["channel"] = cls._sanitize_channel(cls._channel_row(connection, agent_id))
        return json.loads(json.dumps(value, default=str))

    @staticmethod
    def _audit(connection, agent_id, operation, before, after, actor):
        changed_fields = sorted(
            key for key in set(before or {}) | set(after or {})
            if (before or {}).get(key) != (after or {}).get(key)
        )
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value
            ) VALUES ('user', %(actor)s, 'ui', 'agent_asset', %(agent_id)s,
                      %(operation)s, %(fields)s, %(before)s, %(after)s)
            """,
            {
                "actor": actor,
                "agent_id": agent_id,
                "operation": operation,
                "fields": changed_fields,
                "before": Jsonb(before) if before is not None else None,
                "after": Jsonb(after) if after is not None else None,
            },
        )

    @staticmethod
    def _validate_references(
        connection, table, id_column, values, description, extra_clause="", *, lock=False,
    ):
        identifiers = list(dict.fromkeys(UUID(str(value)) for value in (values or [])))
        if not identifiers:
            return []
        rows = connection.execute(
            f"SELECT {id_column} AS identifier FROM {table} "
            f"WHERE {id_column} = ANY(%(identifiers)s) {extra_clause}"
            + (" FOR SHARE" if lock else ""),
            {"identifiers": identifiers},
        ).fetchall()
        if {row["identifier"] for row in rows} != set(identifiers):
            raise ValueError(f"one or more {description} references are invalid")
        return identifiers

    @classmethod
    def _validate_active_org_references(cls, connection, values, current=None):
        person_fields = (
            "business_admin_person_id", "it_admin_person_id", "primary_service_person_id",
        )
        department_fields = ("owning_department_id", "primary_service_department_id")
        for field_name in person_fields:
            if field_name not in values:
                continue
            raw_identifier = values.get(field_name)
            identifier = UUID(str(raw_identifier)) if raw_identifier is not None else None
            if identifier is None or (current is not None and current[field_name] == identifier):
                continue
            cls._validate_references(
                connection, "org.person", "person_id", [identifier],
                field_name, "AND employment_status = 'active'", lock=True,
            )
        for field_name in department_fields:
            if field_name not in values:
                continue
            raw_identifier = values.get(field_name)
            identifier = UUID(str(raw_identifier)) if raw_identifier is not None else None
            if identifier is None or (current is not None and current[field_name] == identifier):
                continue
            cls._validate_references(
                connection, "org.department", "department_id", [identifier],
                field_name, "AND is_active", lock=True,
            )

    @classmethod
    def _validate_shape(cls, connection, values, accessible_person_ids, group_ids, channel):
        role = values["structure_role"]
        if role == "subagent":
            parent_id = values.get("parent_agent_id")
            parent = cls._agent_row(connection, parent_id) if parent_id else None
            if parent is None or parent["structure_role"] != "primary" or parent["lifecycle_status"] == "retired":
                raise ValueError("subagent requires a non-retired primary parent agent")
            if values["service_archetype"] != parent["service_archetype"]:
                raise ValueError("subagent service archetype must match its primary parent")
            if accessible_person_ids or group_ids:
                raise ValueError("subagents inherit accessible people and management groups")
        elif values.get("parent_agent_id") is not None:
            raise ValueError("primary agent cannot have a parent agent")

    @staticmethod
    def _sync_relation(connection, table, id_column, relation_id_column, agent_id, desired_ids):
        current_rows = connection.execute(
            f"SELECT {relation_id_column} AS relation_id FROM {table} "
            "WHERE agent_id = %(agent_id)s AND valid_to IS NULL",
            {"agent_id": agent_id},
        ).fetchall()
        current = {row["relation_id"] for row in current_rows}
        desired = set(desired_ids)
        removed = list(current - desired)
        if removed:
            connection.execute(
                f"UPDATE {table} SET valid_to = GREATEST(clock_timestamp(), valid_from + interval '1 microsecond') "
                f"WHERE agent_id = %(agent_id)s AND {relation_id_column} = ANY(%(removed)s) AND valid_to IS NULL",
                {"agent_id": agent_id, "removed": removed},
            )
        for relation_id in desired - current:
            connection.execute(
                f"INSERT INTO {table} ({id_column}, agent_id, {relation_id_column}) "
                "VALUES (%(row_id)s, %(agent_id)s, %(relation_id)s)",
                {"row_id": uuid4(), "agent_id": agent_id, "relation_id": relation_id},
            )

    @classmethod
    def _save_channel(cls, connection, agent_id, agent_name, channel):
        if channel is None:
            return
        existing = cls._channel_row(connection, agent_id)
        channel_type = channel.get("channel_type", "none")
        if channel_type == "none":
            if existing and existing["enabled"]:
                connection.execute(
                    "UPDATE asset.agent_channel_configuration SET enabled = false "
                    "WHERE agent_channel_configuration_id = %(configuration_id)s",
                    {"configuration_id": existing["agent_channel_configuration_id"]},
                )
            return
        if channel_type != "feishu":
            raise ValueError("unsupported agent channel type")
        application_name = str(channel.get("application_name") or agent_name).strip()
        app_id = str(channel.get("app_id") or "").strip()
        app_secret = channel.get("app_secret")
        if not application_name or not app_id:
            raise ValueError("Feishu application name and AppID are required")
        if existing and existing["app_id"] != app_id and not app_secret:
            raise ValueError("AppSecret is required when changing Feishu AppID")
        encrypted_secret = existing["encrypted_app_secret"] if existing else None
        if app_secret:
            encrypted_secret = CredentialCipher().encrypt({"app_secret": app_secret})
        if not encrypted_secret:
            raise ValueError("Feishu AppSecret is required")
        if existing:
            connection.execute(
                """
                UPDATE asset.agent_channel_configuration
                SET channel_type = 'feishu', application_name = %(application_name)s,
                    app_id = %(app_id)s, encrypted_app_secret = %(encrypted_secret)s,
                    enabled = true
                WHERE agent_channel_configuration_id = %(configuration_id)s
                """,
                {
                    "application_name": application_name,
                    "app_id": app_id,
                    "encrypted_secret": encrypted_secret,
                    "configuration_id": existing["agent_channel_configuration_id"],
                },
            )
        else:
            connection.execute(
                """
                INSERT INTO asset.agent_channel_configuration (
                    agent_channel_configuration_id, agent_id, channel_type,
                    application_name, app_id, encrypted_app_secret
                ) VALUES (%(configuration_id)s, %(agent_id)s, 'feishu',
                          %(application_name)s, %(app_id)s, %(encrypted_secret)s)
                """,
                {
                    "configuration_id": uuid4(),
                    "agent_id": agent_id,
                    "application_name": application_name,
                    "app_id": app_id,
                    "encrypted_secret": encrypted_secret,
                },
            )

    def create_agent(
        self, values: dict[str, Any], accessible_person_ids=None, group_ids=None,
        channel=None, actor="local-user",
    ):
        agent_id = uuid4()
        accessible_person_ids = accessible_person_ids or []
        group_ids = group_ids or []
        with self.database.connection() as connection:
            self._validate_shape(connection, values, accessible_person_ids, group_ids, channel)
            if values["structure_role"] == "subagent":
                active_operation = self._active_deployment_operation(
                    connection, values["parent_agent_id"],
                )
                if active_operation:
                    raise ConflictError(
                        f"parent agent deployment task is active; retry after it finishes: {active_operation['run_id']}"
                    )
            self._validate_active_org_references(connection, values)
            people = self._validate_references(
                connection, "org.person", "person_id", accessible_person_ids,
                "accessible person", "AND employment_status = 'active'", lock=True,
            )
            groups = self._validate_references(
                connection, "asset.management_group", "group_id", group_ids,
                "agent management group", "AND resource_type = 'agent' AND lifecycle_status = 'active'",
            )
            columns = ["agent_id", "structure_role", "parent_agent_id", *AGENT_FIELDS]
            params = {name: values.get(name) for name in columns}
            params["agent_id"] = agent_id
            connection.execute(
                f"INSERT INTO asset.agent_asset ({', '.join(columns)}) "
                f"VALUES ({', '.join(f'%({name})s' for name in columns)})",
                params,
            )
            if values["structure_role"] == "primary":
                self._sync_relation(
                    connection, "asset.agent_accessible_person", "agent_accessible_person_id",
                    "person_id", agent_id, people,
                )
                self._sync_relation(
                    connection, "asset.agent_group_member", "agent_group_member_id",
                    "group_id", agent_id, groups,
                )
            self._save_channel(connection, agent_id, values["name"], channel)
            after = self._audit_value(connection, agent_id)
            self._audit(connection, agent_id, "create", None, after, actor)
        return agent_id

    def update_agent(
        self, agent_id: UUID, values: dict[str, Any], expected_row_version: int,
        accessible_person_ids=None, group_ids=None, channel=None, actor="local-user",
    ):
        with self.database.connection() as connection:
            current = self._agent_row(connection, agent_id)
            if current is None:
                raise NotFoundError("agent not found")
            operation_agent_id = (
                current["parent_agent_id"]
                if current["structure_role"] == "subagent" else current["agent_id"]
            )
            active_operation = self._active_deployment_operation(
                connection, operation_agent_id,
            )
            if active_operation:
                raise ConflictError(
                    f"agent deployment task is active; reload after it finishes: {active_operation['run_id']}"
                )
            before = self._audit_value(connection, agent_id)
            updates = {name: values[name] for name in AGENT_FIELDS if name in values}
            next_values = {**current, **updates}
            next_values["structure_role"] = current["structure_role"]
            next_values["parent_agent_id"] = current["parent_agent_id"]
            if (
                "service_archetype" in updates
                and updates["service_archetype"] != current["service_archetype"]
                and current["lifecycle_status"] != "requested"
            ):
                raise ConflictError("service archetype can only change while agent is requested")
            if "lifecycle_status" in updates and updates["lifecycle_status"] not in STATUS_TRANSITIONS[current["lifecycle_status"]]:
                raise ConflictError("invalid agent lifecycle transition")
            if (
                current["structure_role"] == "primary"
                and updates.get("lifecycle_status") == "retired"
                and connection.execute(
                    """
                    SELECT 1 FROM asset.agent_deployment
                    WHERE primary_agent_id = %(agent_id)s AND lifecycle_status = 'active'
                    """,
                    {"agent_id": agent_id},
                ).fetchone()
            ):
                raise ConflictError("primary agent must remove its active deployment before retirement")
            self._validate_shape(
                connection, next_values,
                accessible_person_ids if accessible_person_ids is not None else [],
                group_ids if group_ids is not None else [],
                channel,
            )
            self._validate_active_org_references(connection, updates, current)
            if current["structure_role"] == "subagent" and (
                accessible_person_ids is not None or group_ids is not None
            ):
                raise ValueError("subagents cannot maintain accessible people or management groups")
            people = None
            if accessible_person_ids is not None:
                people = list(dict.fromkeys(accessible_person_ids))
                current_people = {
                    row["person_id"] for row in connection.execute(
                        "SELECT person_id FROM asset.agent_accessible_person "
                        "WHERE agent_id = %(agent_id)s AND valid_to IS NULL",
                        {"agent_id": agent_id},
                    ).fetchall()
                }
                self._validate_references(
                    connection, "org.person", "person_id", set(people) - current_people,
                    "accessible person", "AND employment_status = 'active'", lock=True,
                )
            groups = None
            if group_ids is not None:
                groups = self._validate_references(
                    connection, "asset.management_group", "group_id", group_ids,
                    "agent management group", "AND resource_type = 'agent' AND lifecycle_status = 'active'",
                )
            assignments = ", ".join(f"{name} = %({name})s" for name in updates) or "name = name"
            result = connection.execute(
                f"UPDATE asset.agent_asset SET {assignments} "
                "WHERE agent_id = %(agent_id)s AND row_version = %(row_version)s",
                {**updates, "agent_id": agent_id, "row_version": expected_row_version},
            )
            if result.rowcount != 1:
                raise ConflictError("agent has changed; reload and retry")
            if people is not None:
                self._sync_relation(
                    connection, "asset.agent_accessible_person", "agent_accessible_person_id",
                    "person_id", agent_id, people,
                )
            if groups is not None:
                self._sync_relation(
                    connection, "asset.agent_group_member", "agent_group_member_id",
                    "group_id", agent_id, groups,
                )
            self._save_channel(connection, agent_id, next_values["name"], channel)
            after = self._audit_value(connection, agent_id)
            self._audit(connection, agent_id, "update", before, after, actor)

    def retire_agent(self, agent_id: UUID, expected_row_version: int, actor="local-user"):
        with self.database.connection() as connection:
            current = self._agent_row(connection, agent_id)
            if current is None:
                raise NotFoundError("agent not found")
            operation_agent_id = (
                current["parent_agent_id"]
                if current["structure_role"] == "subagent" else current["agent_id"]
            )
            active_operation = self._active_deployment_operation(
                connection, operation_agent_id,
            )
            if active_operation:
                raise ConflictError(
                    f"agent deployment task is active; retry after it finishes: {active_operation['run_id']}"
                )
            if current["row_version"] != expected_row_version:
                raise ConflictError("agent has changed; reload and retry")
            if current["structure_role"] == "primary":
                active_deployments = connection.execute(
                    "SELECT count(*) AS count FROM asset.agent_deployment "
                    "WHERE primary_agent_id = %(agent_id)s AND lifecycle_status = 'active'",
                    {"agent_id": agent_id},
                ).fetchone()["count"]
                active_children = connection.execute(
                    "SELECT count(*) AS count FROM asset.agent_asset "
                    "WHERE parent_agent_id = %(agent_id)s AND lifecycle_status <> 'retired'",
                    {"agent_id": agent_id},
                ).fetchone()["count"]
                if active_deployments or active_children:
                    raise ConflictError("primary agent must end active deployment and retire subagents first")
            before = self._audit_value(connection, agent_id)
            result = connection.execute(
                "UPDATE asset.agent_asset SET lifecycle_status = 'retired' "
                "WHERE agent_id = %(agent_id)s AND row_version = %(row_version)s",
                {"agent_id": agent_id, "row_version": expected_row_version},
            )
            if result.rowcount != 1:
                raise ConflictError("agent has changed; reload and retry")
            if current["structure_role"] == "primary":
                self._sync_relation(
                    connection, "asset.agent_accessible_person", "agent_accessible_person_id",
                    "person_id", agent_id, [],
                )
                self._sync_relation(
                    connection, "asset.agent_group_member", "agent_group_member_id",
                    "group_id", agent_id, [],
                )
            self._save_channel(connection, agent_id, current["name"], {"channel_type": "none"})
            after = self._audit_value(connection, agent_id)
            self._audit(connection, agent_id, "retire", before, after, actor)
