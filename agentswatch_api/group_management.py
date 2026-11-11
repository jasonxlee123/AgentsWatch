from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .database import WriteDatabase
from .server_management import ConflictError, NotFoundError


GROUP_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")
GROUP_STATUSES = {"active", "disabled", "retired"}
RESOURCE_CONFIG = {
    "server": ("asset.server_group_member", "server_id", "asset.server_asset", "server_id", None),
    "runtime": ("asset.runtime_group_member", "runtime_id", "asset.runtime_instance", "runtime_id", None),
    "agent": (
        "asset.agent_group_member", "agent_id", "asset.agent_asset", "agent_id",
        "AND structure_role = 'primary'",
    ),
}


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


class GroupManagementService:
    """Manage static operational target groups and their current members."""

    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _group(connection, group_id: UUID, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        return connection.execute(
            """
            SELECT group_id, group_code, name, resource_type::text AS resource_type,
                   lifecycle_status::text AS lifecycle_status, purpose, row_version
            FROM asset.management_group WHERE group_id = %(group_id)s
            """ + suffix,
            {"group_id": group_id},
        ).fetchone()

    @staticmethod
    def _audit_value(connection, group_id: UUID, resource_type: str):
        member_table, member_column, *_ = RESOURCE_CONFIG[resource_type]
        row = connection.execute(
            f"""
            SELECT to_jsonb(group_record) || jsonb_build_object(
                       'member_ids', COALESCE((
                           SELECT jsonb_agg(member.{member_column} ORDER BY member.{member_column})
                           FROM {member_table} member
                           WHERE member.group_id = group_record.group_id AND member.valid_to IS NULL
                       ), '[]'::jsonb)
                   ) AS value
            FROM asset.management_group group_record
            WHERE group_record.group_id = %(group_id)s
            """,
            {"group_id": group_id},
        ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _write_audit(connection, group_id, operation, changed_fields, before, after, actor):
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value
            ) VALUES (
                'user', %(actor)s, 'ui', 'management_group', %(group_id)s,
                %(operation)s, %(changed_fields)s, %(before)s, %(after)s
            )
            """,
            {
                "actor": _required_text(actor, "actor"),
                "group_id": group_id,
                "operation": operation,
                "changed_fields": sorted(set(changed_fields)),
                "before": Jsonb(before) if before is not None else None,
                "after": Jsonb(after) if after is not None else None,
            },
        )

    @staticmethod
    def _validate_members(connection, resource_type: str, member_ids) -> set[UUID]:
        requested = {UUID(str(value)) for value in (member_ids or [])}
        if not requested:
            return requested
        _, _, resource_table, resource_id, extra_clause = RESOURCE_CONFIG[resource_type]
        rows = connection.execute(
            f"SELECT {resource_id} AS member_id FROM {resource_table} "
            f"WHERE {resource_id} = ANY(%(member_ids)s) {extra_clause or ''} FOR SHARE",
            {"member_ids": list(requested)},
        ).fetchall()
        if {row["member_id"] for row in rows} != requested:
            label = "primary agents" if resource_type == "agent" else f"{resource_type}s"
            raise ValueError(f"one or more {label} do not exist or are not eligible")
        return requested

    @classmethod
    def _replace_members(cls, connection, group_id: UUID, resource_type: str, member_ids) -> None:
        member_table, member_column, *_ = RESOURCE_CONFIG[resource_type]
        requested = cls._validate_members(connection, resource_type, member_ids)
        rows = connection.execute(
            f"SELECT {member_column} AS member_id FROM {member_table} "
            "WHERE group_id = %(group_id)s AND valid_to IS NULL FOR UPDATE",
            {"group_id": group_id},
        ).fetchall()
        current = {row["member_id"] for row in rows}
        removed = current - requested
        added = requested - current
        if removed:
            connection.execute(
                f"UPDATE {member_table} SET valid_to = clock_timestamp() "
                f"WHERE group_id = %(group_id)s AND valid_to IS NULL "
                f"AND {member_column} = ANY(%(member_ids)s)",
                {"group_id": group_id, "member_ids": list(removed)},
            )
        for member_id in sorted(added, key=str):
            connection.execute(
                f"INSERT INTO {member_table} (group_id, {member_column}) VALUES (%s, %s)",
                (group_id, member_id),
            )

    def create_group(self, values: dict[str, Any], actor: str) -> UUID:
        group_code = _required_text(values.get("group_code"), "group_code").lower()
        if not GROUP_CODE.fullmatch(group_code):
            raise ValueError("group_code must use lowercase letters, numbers, dot, underscore or hyphen")
        resource_type = values.get("resource_type")
        if resource_type not in RESOURCE_CONFIG:
            raise ValueError("resource_type must be server, runtime or agent")
        name = _required_text(values.get("name"), "name")
        lifecycle_status = values.get("lifecycle_status", "active")
        if lifecycle_status not in GROUP_STATUSES:
            raise ValueError("invalid group lifecycle status")
        purpose = str(values.get("purpose") or "").strip() or None
        group_id = uuid4()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO asset.management_group (
                    group_id, group_code, name, resource_type, purpose, lifecycle_status
                ) VALUES (
                    %(group_id)s, %(group_code)s, %(name)s, %(resource_type)s,
                    %(purpose)s, %(lifecycle_status)s
                )
                """,
                {
                    "group_id": group_id, "group_code": group_code, "name": name,
                    "resource_type": resource_type, "purpose": purpose,
                    "lifecycle_status": lifecycle_status,
                },
            )
            self._replace_members(connection, group_id, resource_type, values.get("member_ids", []))
            after = self._audit_value(connection, group_id, resource_type)
            self._write_audit(
                connection, group_id, "create",
                ("group_code", "name", "resource_type", "purpose", "lifecycle_status", "member_ids"),
                None, after, actor,
            )
        return group_id

    def update_group(
        self, group_id: UUID, values: dict[str, Any], expected_row_version: int, actor: str,
    ) -> None:
        allowed = {"name", "purpose", "lifecycle_status"}
        updates = {field: values[field] for field in allowed if field in values}
        member_ids_present = "member_ids" in values
        if not updates and not member_ids_present:
            raise ValueError("at least one group field or member_ids is required")
        if "name" in updates:
            updates["name"] = _required_text(updates["name"], "name")
        if "purpose" in updates:
            updates["purpose"] = str(updates["purpose"] or "").strip() or None
        if "lifecycle_status" in updates and updates["lifecycle_status"] not in GROUP_STATUSES:
            raise ValueError("invalid group lifecycle status")
        with self.database.connection() as connection:
            current = self._group(connection, group_id, lock=True)
            if current is None:
                raise NotFoundError("management group not found")
            if current["row_version"] != expected_row_version:
                raise ConflictError("management group has changed; reload and retry")
            before = self._audit_value(connection, group_id, current["resource_type"])
            assignments = ", ".join(f"{field} = %({field})s" for field in updates) or "name = name"
            result = connection.execute(
                f"UPDATE asset.management_group SET {assignments} "
                "WHERE group_id = %(group_id)s AND row_version = %(row_version)s",
                {**updates, "group_id": group_id, "row_version": expected_row_version},
            )
            if result.rowcount != 1:
                raise ConflictError("management group has changed; reload and retry")
            if member_ids_present:
                self._replace_members(
                    connection, group_id, current["resource_type"], values.get("member_ids", []),
                )
            after = self._audit_value(connection, group_id, current["resource_type"])
            next_status = updates.get("lifecycle_status", current["lifecycle_status"])
            operation = "retire" if next_status == "retired" else (
                "restore" if current["lifecycle_status"] != "active" and next_status == "active" else "update"
            )
            self._write_audit(
                connection, group_id, operation,
                [*updates, *(["member_ids"] if member_ids_present else [])], before, after, actor,
            )

    def retire_group(self, group_id: UUID, expected_row_version: int, actor: str) -> None:
        current = None
        with self.database.connection() as connection:
            current = self._group(connection, group_id)
        if current is None:
            raise NotFoundError("management group not found")
        self.update_group(
            group_id,
            {"lifecycle_status": "retired", "member_ids": []},
            expected_row_version,
            actor,
        )
