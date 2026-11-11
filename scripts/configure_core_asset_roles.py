#!/usr/bin/env python3
"""Configure least-privilege AgentsWatch PostgreSQL API roles.

All connection details, role names and passwords are read from environment
variables. The command is intended to run after core asset migrations with a
database administrator connection that may manage roles and default privileges.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Mapping

import psycopg
from psycopg import sql


ADMIN_URL_ENV = "AGENTSWATCH_DATABASE_ADMIN_URL"
MIGRATION_OWNER_ENV = "AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE"
READER_ROLE_ENV = "AGENTSWATCH_DATABASE_READER_ROLE"
READER_PASSWORD_ENV = "AGENTSWATCH_DATABASE_READER_PASSWORD"
WRITER_ROLE_ENV = "AGENTSWATCH_DATABASE_WRITER_ROLE"
WRITER_PASSWORD_ENV = "AGENTSWATCH_DATABASE_WRITER_PASSWORD"
LOCK_NAME = "agentswatch-core-assets-role-grants"
ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,62}$")

TABLE_PRIVILEGES = {
    "org": {"reader": ("SELECT",), "writer": ("SELECT", "INSERT", "UPDATE")},
    "asset": {"reader": ("SELECT",), "writer": ("SELECT", "INSERT", "UPDATE")},
    "audit": {"reader": ("SELECT",), "writer": ("SELECT", "INSERT")},
    "ingest": {"reader": ("SELECT",), "writer": ("SELECT", "INSERT", "UPDATE")},
    "automation": {"reader": ("SELECT",), "writer": ("SELECT", "INSERT", "UPDATE")},
    "security": {"reader": (), "writer": ("SELECT", "INSERT", "UPDATE", "DELETE")},
    "integration": {"reader": ("SELECT",), "writer": ("SELECT",)},
}


@dataclass(frozen=True)
class RoleGrantConfig:
    admin_url: str = field(repr=False)
    migration_owner_role: str
    reader_role: str
    reader_password: str = field(repr=False)
    writer_role: str
    writer_password: str = field(repr=False)


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = str(environ.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _required_secret_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if value is None or value == "":
        raise ValueError(f"{name} is required")
    return str(value)


def _validated_role_name(value: str, environment_name: str) -> str:
    if not ROLE_NAME.fullmatch(value):
        raise ValueError(
            f"{environment_name} must be a PostgreSQL role name containing only "
            "letters, numbers, underscore, dot or hyphen"
        )
    if len(value.encode("utf-8")) > 63:
        raise ValueError(f"{environment_name} exceeds PostgreSQL's identifier length")
    return value


def load_config(environ: Mapping[str, str] | None = None) -> RoleGrantConfig:
    values = os.environ if environ is None else environ
    owner = _validated_role_name(
        _required_environment(values, MIGRATION_OWNER_ENV), MIGRATION_OWNER_ENV,
    )
    reader = _validated_role_name(
        _required_environment(values, READER_ROLE_ENV), READER_ROLE_ENV,
    )
    writer = _validated_role_name(
        _required_environment(values, WRITER_ROLE_ENV), WRITER_ROLE_ENV,
    )
    if len({owner, reader, writer}) != 3:
        raise ValueError("migration owner, reader and writer roles must be distinct")
    reader_password = _required_secret_environment(values, READER_PASSWORD_ENV)
    writer_password = _required_secret_environment(values, WRITER_PASSWORD_ENV)
    if len(reader_password) < 16 or len(writer_password) < 16:
        raise ValueError("reader and writer passwords must each contain at least 16 characters")
    if reader_password == writer_password:
        raise ValueError("reader and writer passwords must be different")
    return RoleGrantConfig(
        admin_url=_required_secret_environment(values, ADMIN_URL_ENV),
        migration_owner_role=owner,
        reader_role=reader,
        reader_password=reader_password,
        writer_role=writer,
        writer_password=writer_password,
    )


def _role_row(connection: psycopg.Connection, role_name: str):
    return connection.execute(
        """
        SELECT oid, rolname, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolbypassrls
        FROM pg_roles WHERE rolname = %s
        """,
        (role_name,),
    ).fetchone()


def _owned_object(connection: psycopg.Connection, role_oid: int):
    return connection.execute(
        """
        SELECT object_kind, object_name
        FROM (
            SELECT 'database' AS object_kind, datname AS object_name
            FROM pg_database WHERE datdba = %(role_oid)s
            UNION ALL
            SELECT 'schema', nspname
            FROM pg_namespace WHERE nspowner = %(role_oid)s
            UNION ALL
            SELECT 'relation', namespace.nspname || '.' || class.relname
            FROM pg_class class
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            WHERE class.relowner = %(role_oid)s
            UNION ALL
            SELECT 'routine', namespace.nspname || '.' || procedure.proname
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            WHERE procedure.proowner = %(role_oid)s
            UNION ALL
            SELECT 'type', namespace.nspname || '.' || type.typname
            FROM pg_type type
            JOIN pg_namespace namespace ON namespace.oid = type.typnamespace
            WHERE type.typowner = %(role_oid)s
            UNION ALL
            SELECT 'extension', extname
            FROM pg_extension WHERE extowner = %(role_oid)s
        ) owned
        LIMIT 1
        """,
        {"role_oid": role_oid},
    ).fetchone()


def _preflight(connection: psycopg.Connection, config: RoleGrantConfig) -> str:
    database_name, current_user = connection.execute(
        "SELECT current_database(), current_user"
    ).fetchone()
    if current_user in {config.reader_role, config.writer_role}:
        raise RuntimeError("administrator connection cannot use an API reader or writer role")

    owner = _role_row(connection, config.migration_owner_role)
    if owner is None:
        raise RuntimeError("configured migration owner role does not exist")
    admin = _role_row(connection, current_user)
    owner_authorized = bool(
        admin[2]
        or current_user == config.migration_owner_role
        or connection.execute(
            "SELECT pg_has_role(current_user, %s, 'MEMBER')",
            (config.migration_owner_role,),
        ).fetchone()[0]
    )
    if not owner_authorized:
        raise RuntimeError(
            "administrator must be superuser, migration owner, or a member of the migration owner role"
        )

    for schema_name in TABLE_PRIVILEGES:
        schema_owner = connection.execute(
            """
            SELECT owner.rolname
            FROM pg_namespace namespace
            JOIN pg_roles owner ON owner.oid = namespace.nspowner
            WHERE namespace.nspname = %s
            """,
            (schema_name,),
        ).fetchone()
        if schema_owner is None:
            raise RuntimeError(f"required schema is missing: {schema_name}")
        if schema_owner[0] != config.migration_owner_role:
            raise RuntimeError(
                f"schema {schema_name} is not owned by the configured migration owner"
            )

    for role_name in (config.reader_role, config.writer_role):
        role = _role_row(connection, role_name)
        if role is None:
            continue
        if any(role[index] for index in range(2, 7)):
            raise RuntimeError(f"refusing to repurpose privileged role: {role_name}")
        membership = connection.execute(
            """
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = %s
            LIMIT 1
            """,
            (role_name,),
        ).fetchone()
        if membership is not None:
            raise RuntimeError(f"refusing to repurpose role with memberships: {role_name}")
        if _owned_object(connection, role[0]) is not None:
            raise RuntimeError(f"refusing to repurpose role that owns database objects: {role_name}")
    return database_name


def _ensure_login_role(
    connection: psycopg.Connection,
    role_name: str,
    password: str,
    *,
    default_read_only: bool,
) -> None:
    if _role_row(connection, role_name) is None:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(role_name))
        )
    password_verifier = connection.pgconn.encrypt_password(
        password.encode("utf-8"),
        role_name.encode("utf-8"),
        b"scram-sha-256",
    ).decode("ascii")
    connection.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(role_name), sql.Literal(password_verifier))
    )
    connection.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(sql.Identifier(role_name)))
    connection.execute(
        sql.SQL("ALTER ROLE {} SET default_transaction_read_only = {}").format(
            sql.Identifier(role_name),
            sql.SQL("on" if default_read_only else "off"),
        )
    )
    connection.execute(
        sql.SQL("ALTER ROLE {} SET search_path = pg_catalog, public").format(
            sql.Identifier(role_name)
        )
    )


def _privilege_list(privileges: tuple[str, ...]) -> sql.Composed:
    return sql.SQL(", ").join(sql.SQL(item) for item in privileges)


def _reset_current_privileges(
    connection: psycopg.Connection,
    database_name: str,
    config: RoleGrantConfig,
) -> None:
    database = sql.Identifier(database_name)
    reader = sql.Identifier(config.reader_role)
    writer = sql.Identifier(config.writer_role)
    connection.execute(
        sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC").format(database)
    )
    connection.execute(
        sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}, {}").format(
            database, reader, writer,
        )
    )
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(database, reader, writer)
    )

    connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    connection.execute(
        sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}, {}").format(reader, writer)
    )
    connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(reader, writer)
    )

    for schema_name, grants in TABLE_PRIVILEGES.items():
        schema = sql.Identifier(schema_name)
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM PUBLIC").format(schema)
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}, {}").format(
                schema, reader, writer,
            )
        )
        for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            connection.execute(
                sql.SQL(
                    f"REVOKE ALL PRIVILEGES ON ALL {object_kind} IN SCHEMA {{}} FROM PUBLIC"
                ).format(schema)
            )
            connection.execute(
                sql.SQL(
                    f"REVOKE ALL PRIVILEGES ON ALL {object_kind} IN SCHEMA {{}} FROM {{}}, {{}}"
                ).format(schema, reader, writer)
            )
        for role_kind, role_identifier in (("reader", reader), ("writer", writer)):
            privileges = grants[role_kind]
            if privileges:
                connection.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role_identifier)
                )
                connection.execute(
                    sql.SQL("GRANT {} ON ALL TABLES IN SCHEMA {} TO {}").format(
                        _privilege_list(privileges), schema, role_identifier,
                    )
                )


def _reset_default_privileges(
    connection: psycopg.Connection,
    config: RoleGrantConfig,
) -> None:
    owner = sql.Identifier(config.migration_owner_role)
    reader = sql.Identifier(config.reader_role)
    writer = sql.Identifier(config.writer_role)
    # PostgreSQL grants EXECUTE on new functions to PUBLIC as a global default.
    # A per-schema REVOKE cannot override that global default, so remove it for
    # the dedicated migration owner before applying schema-local API grants.
    connection.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} "
            "REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC"
        ).format(owner)
    )
    for schema_name, grants in TABLE_PRIVILEGES.items():
        schema = sql.Identifier(schema_name)
        for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            connection.execute(
                sql.SQL(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {{}} IN SCHEMA {{}} "
                    f"REVOKE ALL PRIVILEGES ON {object_kind} FROM PUBLIC"
                ).format(owner, schema)
            )
            connection.execute(
                sql.SQL(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {{}} IN SCHEMA {{}} "
                    f"REVOKE ALL PRIVILEGES ON {object_kind} FROM {{}}, {{}}"
                ).format(owner, schema, reader, writer)
            )
        for role_kind, role_identifier in (("reader", reader), ("writer", writer)):
            privileges = grants[role_kind]
            if privileges:
                connection.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                        "GRANT {} ON TABLES TO {}"
                    ).format(owner, schema, _privilege_list(privileges), role_identifier)
                )


def _verify_roles(
    connection: psycopg.Connection,
    database_name: str,
    config: RoleGrantConfig,
) -> None:
    for role_name in (config.reader_role, config.writer_role):
        role = _role_row(connection, role_name)
        if role is None or any(role[index] for index in range(2, 7)):
            raise RuntimeError("API database role has unsafe PostgreSQL attributes")
        login_policy = connection.execute(
            """
            SELECT rolcanlogin, rolinherit, coalesce(rolconfig, '{}')
            FROM pg_roles WHERE rolname = %s
            """,
            (role_name,),
        ).fetchone()
        expected_read_only = (
            "default_transaction_read_only=on"
            if role_name == config.reader_role
            else "default_transaction_read_only=off"
        )
        if (
            login_policy is None
            or login_policy[0] is not True
            or login_policy[1] is not False
            or expected_read_only not in login_policy[2]
        ):
            raise RuntimeError("API database role login policy is unsafe")
        database_privileges = connection.execute(
            """
            SELECT has_database_privilege(%(role)s, %(database)s, 'CONNECT'),
                   has_database_privilege(%(role)s, %(database)s, 'CREATE'),
                   has_database_privilege(%(role)s, %(database)s, 'TEMPORARY')
            """,
            {"role": role_name, "database": database_name},
        ).fetchone()
        if database_privileges != (True, False, False):
            raise RuntimeError("API database role has unsafe database privileges")

    privilege_names = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    rows = connection.execute(
        """
        SELECT namespace.nspname, class.oid
        FROM pg_class class
        JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = ANY(%s)
          AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
        """,
        (list(TABLE_PRIVILEGES),),
    ).fetchall()
    for schema_name, relation_oid in rows:
        for role_kind, role_name in (
            ("reader", config.reader_role), ("writer", config.writer_role),
        ):
            expected = set(TABLE_PRIVILEGES[schema_name][role_kind])
            actual = {
                privilege
                for privilege in privilege_names
                if connection.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    (role_name, relation_oid, privilege),
                ).fetchone()[0]
            }
            if actual != expected:
                raise RuntimeError("API database role table privileges do not match policy")


def configure_roles(connection: psycopg.Connection, config: RoleGrantConfig) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_NAME,))
    database_name = _preflight(connection, config)
    connection.execute("SET LOCAL password_encryption = 'scram-sha-256'")
    _ensure_login_role(
        connection, config.reader_role, config.reader_password, default_read_only=True,
    )
    _ensure_login_role(
        connection, config.writer_role, config.writer_password, default_read_only=False,
    )
    _reset_current_privileges(connection, database_name, config)
    _reset_default_privileges(connection, config)
    _verify_roles(connection, database_name, config)


def main() -> int:
    try:
        config = load_config()
        with psycopg.connect(config.admin_url) as connection:
            configure_roles(connection, config)
        print("AgentsWatch database API roles configured")
        return 0
    except psycopg.Error as exc:
        message = exc.diag.message_primary or "PostgreSQL role configuration failed"
        print(f"database role configuration failed: {message}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"database role configuration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
