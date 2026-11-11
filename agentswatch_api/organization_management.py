from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .database import WriteDatabase
from .server_management import ConflictError, NotFoundError


DEPARTMENT_FIELDS = {"name", "parent_department_id", "is_active"}
PERSON_FIELDS = {"display_name", "primary_department_id", "employment_status"}
EMPLOYMENT_STATUSES = {"active", "inactive", "departed"}


def normalize_required_text(value: Any, field_name: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


class OrganizationManagementService:
    """Transactional management of organization master data.

    Department codes and employee numbers are external identities. They are
    accepted only on create and are deliberately absent from update fields.
    """

    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _write_audit(
        connection,
        object_type: str,
        object_id: UUID,
        operation: str,
        changed_fields,
        before_value,
        after_value,
        actor: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id,
                operation, changed_fields, before_value, after_value
            ) VALUES (
                'user', %(actor)s, 'ui', %(object_type)s, %(object_id)s,
                %(operation)s, %(changed_fields)s, %(before_value)s, %(after_value)s
            )
            """,
            {
                "actor": normalize_required_text(actor, "actor"),
                "object_type": object_type,
                "object_id": object_id,
                "operation": operation,
                "changed_fields": sorted(set(changed_fields)),
                "before_value": Jsonb(before_value) if before_value is not None else None,
                "after_value": Jsonb(after_value) if after_value is not None else None,
            },
        )

    @staticmethod
    def _record_json(connection, table: str, id_column: str, object_id: UUID):
        # table/id_column are internal constants, never request-controlled.
        row = connection.execute(
            f"SELECT to_jsonb(record) AS value FROM {table} record "
            f"WHERE {id_column} = %(object_id)s",
            {"object_id": object_id},
        ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _department(connection, department_id: UUID, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        return connection.execute(
            """
            SELECT department_id, department_code, name, parent_department_id,
                   is_active, row_version
            FROM org.department WHERE department_id = %(department_id)s
            """ + suffix,
            {"department_id": department_id},
        ).fetchone()

    @staticmethod
    def _person(connection, person_id: UUID, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        return connection.execute(
            """
            SELECT person_id, employee_no, display_name, primary_department_id,
                   employment_status, row_version
            FROM org.person WHERE person_id = %(person_id)s
            """ + suffix,
            {"person_id": person_id},
        ).fetchone()

    @staticmethod
    def _validate_parent(
        connection,
        parent_department_id: UUID | None,
        *,
        department_id: UUID | None = None,
        require_active: bool = True,
    ) -> None:
        if parent_department_id is None:
            return
        parent = connection.execute(
            "SELECT department_id, is_active FROM org.department "
            "WHERE department_id = %s FOR SHARE",
            (parent_department_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("parent department does not exist")
        if require_active and not parent["is_active"]:
            raise ValueError("active department requires an active parent department")
        if department_id is None:
            return
        if parent_department_id == department_id:
            raise ValueError("department cannot be its own parent")
        descendant = connection.execute(
            """
            WITH RECURSIVE descendants AS (
                SELECT department_id
                FROM org.department
                WHERE parent_department_id = %(department_id)s
                UNION ALL
                SELECT child.department_id
                FROM org.department child
                JOIN descendants parent
                  ON child.parent_department_id = parent.department_id
            )
            SELECT 1 FROM descendants WHERE department_id = %(parent_id)s LIMIT 1
            """,
            {"department_id": department_id, "parent_id": parent_department_id},
        ).fetchone()
        if descendant:
            raise ValueError("department hierarchy cannot contain a cycle")

    @staticmethod
    def _validate_department_can_deactivate(connection, department_id: UUID) -> None:
        active_child = connection.execute(
            """
            SELECT name FROM org.department
            WHERE parent_department_id = %(department_id)s AND is_active
            LIMIT 1
            """,
            {"department_id": department_id},
        ).fetchone()
        if active_child:
            raise ConflictError("department has active child departments; deactivate or move them first")
        active_person = connection.execute(
            """
            SELECT display_name FROM org.person
            WHERE primary_department_id = %(department_id)s
              AND employment_status = 'active'
            LIMIT 1
            """,
            {"department_id": department_id},
        ).fetchone()
        if active_person:
            raise ConflictError("department has active people; move or deactivate them first")
        active_agent = connection.execute(
            """
            SELECT name FROM asset.agent_asset
            WHERE lifecycle_status <> 'retired'
              AND (
                  owning_department_id = %(department_id)s
                  OR primary_service_department_id = %(department_id)s
              )
            LIMIT 1
            """,
            {"department_id": department_id},
        ).fetchone()
        if active_agent:
            raise ConflictError("department is referenced by a non-retired agent; reassign the agent first")

    @staticmethod
    def _validate_person_can_deactivate(connection, person_id: UUID) -> None:
        active_agent = connection.execute(
            """
            SELECT name FROM asset.agent_asset
            WHERE lifecycle_status <> 'retired'
              AND (
                  business_admin_person_id = %(person_id)s
                  OR it_admin_person_id = %(person_id)s
                  OR primary_service_person_id = %(person_id)s
                  OR EXISTS (
                      SELECT 1 FROM asset.agent_accessible_person access
                      WHERE access.agent_id = agent_asset.agent_id
                        AND access.person_id = %(person_id)s
                        AND access.valid_to IS NULL
                  )
              )
            LIMIT 1
            """,
            {"person_id": person_id},
        ).fetchone()
        if active_agent:
            raise ConflictError("person is referenced by a non-retired agent; reassign the agent first")

    @staticmethod
    def _validate_person_department(
        connection,
        department_id: UUID | None,
        employment_status: str,
    ) -> None:
        if department_id is None:
            return
        department = connection.execute(
            "SELECT is_active FROM org.department WHERE department_id = %s FOR SHARE",
            (department_id,),
        ).fetchone()
        if department is None:
            raise ValueError("primary department does not exist")
        if employment_status == "active" and not department["is_active"]:
            raise ValueError("active person requires an active primary department")

    def create_department(self, values: dict[str, Any], actor: str) -> UUID:
        department_code = normalize_required_text(values.get("department_code"), "department_code")
        name = normalize_required_text(values.get("name"), "name")
        parent_id = values.get("parent_department_id")
        is_active = values.get("is_active", True)
        department_id = uuid4()
        with self.database.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext('org-department-hierarchy'))")
            self._validate_parent(
                connection, parent_id, department_id=department_id, require_active=is_active,
            )
            connection.execute(
                """
                INSERT INTO org.department (
                    department_id, department_code, name, parent_department_id, is_active
                ) VALUES (
                    %(department_id)s, %(department_code)s, %(name)s,
                    %(parent_department_id)s, %(is_active)s
                )
                """,
                {
                    "department_id": department_id,
                    "department_code": department_code,
                    "name": name,
                    "parent_department_id": parent_id,
                    "is_active": is_active,
                },
            )
            after = self._record_json(connection, "org.department", "department_id", department_id)
            self._write_audit(
                connection, "department", department_id, "create",
                ("department_code", "name", "parent_department_id", "is_active"),
                None, after, actor,
            )
        return department_id

    def update_department(
        self,
        department_id: UUID,
        values: dict[str, Any],
        expected_row_version: int,
        actor: str,
    ) -> None:
        updates = {field: values[field] for field in DEPARTMENT_FIELDS if field in values}
        if not updates:
            raise ValueError("at least one department field is required")
        if "name" in updates:
            updates["name"] = normalize_required_text(updates["name"], "name")
        with self.database.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext('org-department-hierarchy'))")
            current = self._department(connection, department_id, lock=True)
            if current is None:
                raise NotFoundError("department not found")
            if current["row_version"] != expected_row_version:
                raise ConflictError("department has changed; reload and retry")
            next_active = updates.get("is_active", current["is_active"])
            next_parent = updates.get("parent_department_id", current["parent_department_id"])
            self._validate_parent(
                connection, next_parent, department_id=department_id,
                require_active=next_active,
            )
            if current["is_active"] and not next_active:
                self._validate_department_can_deactivate(connection, department_id)
            before = self._record_json(connection, "org.department", "department_id", department_id)
            assignments = ", ".join(f"{field} = %({field})s" for field in updates)
            result = connection.execute(
                f"UPDATE org.department SET {assignments} "
                "WHERE department_id = %(department_id)s AND row_version = %(row_version)s",
                {**updates, "department_id": department_id, "row_version": expected_row_version},
            )
            if result.rowcount != 1:
                raise ConflictError("department has changed; reload and retry")
            after = self._record_json(connection, "org.department", "department_id", department_id)
            operation = "restore" if not current["is_active"] and next_active else (
                "retire" if current["is_active"] and not next_active else "update"
            )
            self._write_audit(
                connection, "department", department_id, operation,
                updates.keys(), before, after, actor,
            )

    def deactivate_department(
        self, department_id: UUID, expected_row_version: int, actor: str,
    ) -> None:
        self.update_department(
            department_id, {"is_active": False}, expected_row_version, actor,
        )

    def create_person(self, values: dict[str, Any], actor: str) -> UUID:
        employee_no = normalize_required_text(values.get("employee_no"), "employee_no")
        display_name = normalize_required_text(values.get("display_name"), "display_name")
        department_id = values.get("primary_department_id")
        employment_status = values.get("employment_status", "active")
        if employment_status not in EMPLOYMENT_STATUSES:
            raise ValueError("invalid employment status")
        person_id = uuid4()
        with self.database.connection() as connection:
            self._validate_person_department(connection, department_id, employment_status)
            connection.execute(
                """
                INSERT INTO org.person (
                    person_id, employee_no, display_name,
                    primary_department_id, employment_status
                ) VALUES (
                    %(person_id)s, %(employee_no)s, %(display_name)s,
                    %(primary_department_id)s, %(employment_status)s
                )
                """,
                {
                    "person_id": person_id,
                    "employee_no": employee_no,
                    "display_name": display_name,
                    "primary_department_id": department_id,
                    "employment_status": employment_status,
                },
            )
            after = self._record_json(connection, "org.person", "person_id", person_id)
            self._write_audit(
                connection, "person", person_id, "create",
                ("employee_no", "display_name", "primary_department_id", "employment_status"),
                None, after, actor,
            )
        return person_id

    def update_person(
        self,
        person_id: UUID,
        values: dict[str, Any],
        expected_row_version: int,
        actor: str,
    ) -> None:
        updates = {field: values[field] for field in PERSON_FIELDS if field in values}
        if not updates:
            raise ValueError("at least one person field is required")
        if "display_name" in updates:
            updates["display_name"] = normalize_required_text(updates["display_name"], "display_name")
        if "employment_status" in updates and updates["employment_status"] not in EMPLOYMENT_STATUSES:
            raise ValueError("invalid employment status")
        with self.database.connection() as connection:
            current = self._person(connection, person_id, lock=True)
            if current is None:
                raise NotFoundError("person not found")
            if current["row_version"] != expected_row_version:
                raise ConflictError("person has changed; reload and retry")
            next_status = updates.get("employment_status", current["employment_status"])
            next_department = updates.get("primary_department_id", current["primary_department_id"])
            self._validate_person_department(connection, next_department, next_status)
            if current["employment_status"] == "active" and next_status != "active":
                self._validate_person_can_deactivate(connection, person_id)
            before = self._record_json(connection, "org.person", "person_id", person_id)
            assignments = ", ".join(f"{field} = %({field})s" for field in updates)
            result = connection.execute(
                f"UPDATE org.person SET {assignments} "
                "WHERE person_id = %(person_id)s AND row_version = %(row_version)s",
                {**updates, "person_id": person_id, "row_version": expected_row_version},
            )
            if result.rowcount != 1:
                raise ConflictError("person has changed; reload and retry")
            after = self._record_json(connection, "org.person", "person_id", person_id)
            operation = "restore" if current["employment_status"] != "active" and next_status == "active" else (
                "retire" if current["employment_status"] == "active" and next_status != "active" else "update"
            )
            self._write_audit(
                connection, "person", person_id, operation,
                updates.keys(), before, after, actor,
            )

    def deactivate_person(
        self, person_id: UUID, expected_row_version: int, actor: str,
    ) -> None:
        self.update_person(
            person_id, {"employment_status": "inactive"}, expected_row_version, actor,
        )
