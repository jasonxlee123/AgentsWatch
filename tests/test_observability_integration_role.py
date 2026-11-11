import os
import unittest

import psycopg

from scripts.configure_observability_integration_role import (
    ADMIN_URL_ENV,
    COLUMN_PRIVILEGES,
    OWNER_ROLE_ENV,
    PASSWORD_ENV,
    ROLE_ENV,
    SCHEMAS,
    SEQUENCE_PRIVILEGES,
    TABLE_PRIVILEGES,
    configure,
    load_config,
)


ROLE_TEST_URL_ENV = "AGENTSWATCH_OBSERVABILITY_ROLE_TEST_URL"


class ObservabilityIntegrationRoleConfigTests(unittest.TestCase):
    def valid(self):
        return {
            ADMIN_URL_ENV: "postgresql://admin@localhost/agentswatch",
            OWNER_ROLE_ENV: "agentswatch_owner",
            ROLE_ENV: "agentswatch_observability",
            PASSWORD_ENV: "long-observability-password",
        }

    def test_loads_distinct_restricted_role_configuration(self):
        config = load_config(self.valid())
        self.assertEqual(config.owner_role, "agentswatch_owner")
        self.assertEqual(config.role, "agentswatch_observability")
        self.assertNotIn(config.password, repr(config))

    def test_rejects_owner_reuse_invalid_name_and_short_password(self):
        values = self.valid()
        values[ROLE_ENV] = values[OWNER_ROLE_ENV]
        with self.assertRaisesRegex(ValueError, "must differ"):
            load_config(values)

        values = self.valid()
        values[ROLE_ENV] = "bad role"
        with self.assertRaisesRegex(ValueError, "valid PostgreSQL"):
            load_config(values)

        values = self.valid()
        values[PASSWORD_ENV] = "too-short"
        with self.assertRaisesRegex(ValueError, "at least 16"):
            load_config(values)


@unittest.skipUnless(
    all(
        os.environ.get(name)
        for name in (
            ADMIN_URL_ENV,
            OWNER_ROLE_ENV,
            ROLE_ENV,
            PASSWORD_ENV,
            ROLE_TEST_URL_ENV,
        )
    ),
    "isolated observability role-grant test environment is not configured",
)
class ObservabilityIntegrationRoleGrantTests(unittest.TestCase):
    def assert_statement_denied(self, connection, statement):
        with self.assertRaises(psycopg.Error) as caught:
            connection.execute(statement)
        self.assertEqual(caught.exception.sqlstate, "42501")

    def test_object_acls_enforce_business_read_only_after_default_override(self):
        role = os.environ[ROLE_ENV]
        with psycopg.connect(os.environ[ADMIN_URL_ENV], autocommit=True) as connection:
            effective = []
            relations = connection.execute(
                """
                SELECT namespace.nspname, class.relname, class.oid
                FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = ANY(%s)
                  AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
                ORDER BY namespace.nspname, class.relname
                """,
                (list(SCHEMAS),),
            ).fetchall()
            for schema_name, relation_name, relation_oid in relations:
                for privilege in TABLE_PRIVILEGES:
                    if connection.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (role, relation_oid, privilege),
                    ).fetchone()[0]:
                        effective.append((schema_name, relation_name, privilege))
            self.assertEqual(
                effective,
                [("integration", "observability_gateway_v2", "SELECT")],
            )
            unexpected_columns = connection.execute(
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
                """,
                (list(COLUMN_PRIVILEGES), list(SCHEMAS), role),
            ).fetchall()
            self.assertEqual(unexpected_columns, [])
            unexpected_sequences = connection.execute(
                """
                SELECT namespace.nspname, class.relname, privilege.name
                FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                CROSS JOIN unnest(%s::text[]) AS privilege(name)
                WHERE namespace.nspname = ANY(%s)
                  AND class.relkind = 'S'
                  AND has_sequence_privilege(%s, class.oid, privilege.name)
                """,
                (list(SEQUENCE_PRIVILEGES), list(SCHEMAS), role),
            ).fetchall()
            self.assertEqual(unexpected_sequences, [])
            self.assertFalse(
                any(
                    connection.execute(
                        "SELECT has_schema_privilege(%s, %s, 'CREATE')",
                        (role, schema_name),
                    ).fetchone()[0]
                    for schema_name in SCHEMAS
                )
            )

        with psycopg.connect(
            os.environ[ROLE_TEST_URL_ENV], autocommit=True
        ) as connection:
            self.assertEqual(
                connection.execute("SHOW default_transaction_read_only").fetchone()[0],
                "on",
            )
            connection.execute("SET default_transaction_read_only = off")
            self.assertEqual(
                connection.execute("SHOW default_transaction_read_only").fetchone()[0],
                "off",
            )
            connection.execute(
                "SELECT count(*) FROM integration.observability_gateway_v2"
            ).fetchone()
            self.assert_statement_denied(
                connection, "SELECT count(*) FROM integration.observability_gateway_v1"
            )
            self.assert_statement_denied(
                connection, "SELECT count(*) FROM asset.runtime_instance"
            )
            self.assert_statement_denied(
                connection,
                "SELECT encrypted_password FROM security.server_ssh_credential",
            )
            self.assert_statement_denied(
                connection,
                "INSERT INTO asset.environment (environment_code, name, stage) "
                "VALUES ('observability-forbidden', 'forbidden', 'test')",
            )

    def test_effective_security_definer_execution_is_rejected(self):
        function_name = "integration.observability_role_security_definer_probe"
        with psycopg.connect(os.environ[ADMIN_URL_ENV], autocommit=True) as connection:
            connection.execute(
                f"CREATE FUNCTION {function_name}() RETURNS integer "
                "LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
            )
            connection.execute(f"GRANT EXECUTE ON FUNCTION {function_name}() TO PUBLIC")
        try:
            with self.assertRaisesRegex(RuntimeError, "SECURITY DEFINER"):
                configure(load_config(os.environ))
        finally:
            with psycopg.connect(
                os.environ[ADMIN_URL_ENV], autocommit=True
            ) as connection:
                connection.execute(f"DROP FUNCTION {function_name}()")


if __name__ == "__main__":
    unittest.main()
