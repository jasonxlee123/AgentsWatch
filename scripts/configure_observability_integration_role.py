#!/usr/bin/env python3
"""Create the database login used only by the observability v2 contract."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping

import psycopg
from psycopg import sql


ROLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,62}$")
ADMIN_URL_ENV = "AGENTSWATCH_DATABASE_ADMIN_URL"
OWNER_ROLE_ENV = "AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE"
ROLE_ENV = "AGENTSWATCH_OBSERVABILITY_DATABASE_ROLE"
PASSWORD_ENV = "AGENTSWATCH_OBSERVABILITY_DATABASE_PASSWORD"
LOCK_NAME = "agentswatch-observability-integration-role"
SCHEMAS = ("public", "org", "asset", "audit", "ingest", "automation", "security", "integration")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SEQUENCE_PRIVILEGES = ("SELECT", "USAGE", "UPDATE")


@dataclass(frozen=True)
class IntegrationRoleConfig:
    admin_url: str = field(repr=False)
    owner_role: str
    role: str
    password: str = field(repr=False)


def _required(values: Mapping[str, str], name: str) -> str:
    value = str(values.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _role_name(values: Mapping[str, str], name: str) -> str:
    value = _required(values, name)
    if not ROLE_PATTERN.fullmatch(value) or len(value.encode("utf-8")) > 63:
        raise ValueError(f"{name} is not a valid PostgreSQL role name")
    return value


def load_config(environ: Mapping[str, str] | None = None) -> IntegrationRoleConfig:
    values = os.environ if environ is None else environ
    owner = _role_name(values, OWNER_ROLE_ENV)
    role = _role_name(values, ROLE_ENV)
    if role == owner:
        raise ValueError("observability database role must differ from migration owner")
    password = values.get(PASSWORD_ENV, "")
    if len(password) < 16:
        raise ValueError(f"{PASSWORD_ENV} must contain at least 16 characters")
    return IntegrationRoleConfig(
        admin_url=_required(values, ADMIN_URL_ENV),
        owner_role=owner,
        role=role,
        password=password,
    )


def _role(connection: psycopg.Connection, role_name: str):
    return connection.execute(
        """
        SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
        FROM pg_roles WHERE rolname = %s
        """,
        (role_name,),
    ).fetchone()


def _preflight(connection: psycopg.Connection, config: IntegrationRoleConfig) -> str:
    database_name, current_user = connection.execute(
        "SELECT current_database(), current_user"
    ).fetchone()
    if connection.execute(
        "SELECT to_regclass('integration.observability_gateway_v2')"
    ).fetchone()[0] is None:
        raise RuntimeError("integration.observability_gateway_v2 is not installed")
    owner = _role(connection, config.owner_role)
    admin = _role(connection, current_user)
    if owner is None or admin is None:
        raise RuntimeError("migration owner or administrator role does not exist")
    if not (
        admin[1]
        or current_user == config.owner_role
        or connection.execute(
            "SELECT pg_has_role(current_user, %s, 'MEMBER')", (config.owner_role,)
        ).fetchone()[0]
    ):
        raise RuntimeError("administrator is not authorized by the migration owner")
    existing = _role(connection, config.role)
    if existing is not None:
        if any(existing[1:]):
            raise RuntimeError("refusing to repurpose a privileged database role")
        if connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_auth_members membership
                WHERE membership.member = %s OR membership.roleid = %s
            )
            """,
            (existing[0], existing[0]),
        ).fetchone()[0]:
            raise RuntimeError("refusing to repurpose a role with memberships")
    return database_name


def configure(config: IntegrationRoleConfig) -> None:
    with psycopg.connect(config.admin_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_NAME,))
        database_name = _preflight(connection, config)
        role_identifier = sql.Identifier(config.role)
        if _role(connection, config.role) is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(role_identifier)
            )
        verifier = connection.pgconn.encrypt_password(
            config.password.encode(), config.role.encode(), b"scram-sha-256"
        ).decode()
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(role_identifier, sql.Literal(verifier))
        )
        connection.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(role_identifier))
        connection.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                role_identifier
            )
        )
        connection.execute(
            sql.SQL("ALTER ROLE {} SET search_path = pg_catalog").format(role_identifier)
        )

        database_identifier = sql.Identifier(database_name)
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
                database_identifier, role_identifier
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                database_identifier, role_identifier
            )
        )
        for schema_name in SCHEMAS:
            schema_identifier = sql.Identifier(schema_name)
            connection.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                    schema_identifier, role_identifier
                )
            )
            for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
                connection.execute(
                    sql.SQL(
                        f"REVOKE ALL PRIVILEGES ON ALL {object_kind} IN SCHEMA {{}} FROM {{}}"
                    ).format(schema_identifier, role_identifier)
                )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA integration TO {}").format(role_identifier)
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT ON integration.observability_gateway_v2 TO {}"
            ).format(role_identifier)
        )

        unexpected_relation_privileges = connection.execute(
            """
            SELECT namespace.nspname, class.relname, privilege.name
            FROM pg_class class
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE namespace.nspname = ANY(%s)
              AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND has_table_privilege(%s, class.oid, privilege.name)
              AND NOT (
                  namespace.nspname = 'integration'
                  AND class.relname = 'observability_gateway_v2'
                  AND privilege.name = 'SELECT'
              )
            LIMIT 1
            """,
            (list(TABLE_PRIVILEGES), list(SCHEMAS), config.role),
        ).fetchone()
        permitted = connection.execute(
            "SELECT has_table_privilege(%s, 'integration.observability_gateway_v2', 'SELECT')",
            (config.role,),
        ).fetchone()[0]
        unexpected_column_privileges = connection.execute(
            """
            SELECT namespace.nspname, class.relname, attribute.attname, privilege.name
            FROM pg_class class
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            JOIN pg_attribute attribute ON attribute.attrelid = class.oid
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE namespace.nspname = ANY(%s)
              AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND has_column_privilege(
                  %s, class.oid, attribute.attnum, privilege.name
              )
              AND NOT (
                  namespace.nspname = 'integration'
                  AND class.relname = 'observability_gateway_v2'
                  AND privilege.name = 'SELECT'
              )
            LIMIT 1
            """,
            (list(COLUMN_PRIVILEGES), list(SCHEMAS), config.role),
        ).fetchone()
        unexpected_sequence_privileges = connection.execute(
            """
            SELECT namespace.nspname, class.relname, privilege.name
            FROM pg_class class
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE namespace.nspname = ANY(%s)
              AND class.relkind = 'S'
              AND has_sequence_privilege(%s, class.oid, privilege.name)
            LIMIT 1
            """,
            (list(SEQUENCE_PRIVILEGES), list(SCHEMAS), config.role),
        ).fetchone()
        unexpected_schema_create = connection.execute(
            """
            SELECT schema_name
            FROM unnest(%s::text[]) AS schema_name
            WHERE has_schema_privilege(%s, schema_name, 'CREATE')
            LIMIT 1
            """,
            (list(SCHEMAS), config.role),
        ).fetchone()
        if (
            not permitted
            or unexpected_relation_privileges is not None
            or unexpected_column_privileges is not None
            or unexpected_sequence_privileges is not None
            or unexpected_schema_create is not None
        ):
            raise RuntimeError("observability integration role verification failed")

        # ``default_transaction_read_only`` is an accident-prevention default,
        # not a privilege boundary: a login may change its own session default.
        # Object ACLs above enforce business-data read-only access.  Also fail
        # closed if PUBLIC (or any other effective grant) exposes a
        # SECURITY DEFINER function to this role, because direct REVOKE cannot
        # override privileges inherited from PUBLIC.
        effective_security_definer = connection.execute(
            """
            SELECT 1
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = ANY(%s)
              AND procedure.prosecdef
              AND has_function_privilege(%s, procedure.oid, 'EXECUTE')
            LIMIT 1
            """,
            (list(SCHEMAS), config.role),
        ).fetchone()
        if effective_security_definer is not None:
            raise RuntimeError(
                "observability integration role can execute a SECURITY DEFINER function"
            )


def main() -> None:
    config = load_config()
    configure(config)
    print(f"configured restricted observability database role: {config.role}")


if __name__ == "__main__":
    main()
