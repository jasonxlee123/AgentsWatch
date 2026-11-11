import os
import unittest
from uuid import uuid4

import psycopg


ADMIN_URL_ENV = "AGENTSWATCH_ROLE_GRANT_TEST_ADMIN_URL"
READER_URL_ENV = "AGENTSWATCH_ROLE_GRANT_TEST_READER_URL"
WRITER_URL_ENV = "AGENTSWATCH_ROLE_GRANT_TEST_WRITER_URL"
READER_ROLE_ENV = "AGENTSWATCH_ROLE_GRANT_TEST_READER_ROLE"
WRITER_ROLE_ENV = "AGENTSWATCH_ROLE_GRANT_TEST_WRITER_ROLE"
REQUIRED_ENVIRONMENT = (
    ADMIN_URL_ENV, READER_URL_ENV, WRITER_URL_ENV, READER_ROLE_ENV, WRITER_ROLE_ENV,
)

TABLE_POLICY = {
    "org": {"reader": {"SELECT"}, "writer": {"SELECT", "INSERT", "UPDATE"}},
    "asset": {"reader": {"SELECT"}, "writer": {"SELECT", "INSERT", "UPDATE"}},
    "audit": {"reader": {"SELECT"}, "writer": {"SELECT", "INSERT"}},
    "ingest": {"reader": {"SELECT"}, "writer": {"SELECT", "INSERT", "UPDATE"}},
    "automation": {"reader": {"SELECT"}, "writer": {"SELECT", "INSERT", "UPDATE"}},
    "security": {"reader": set(), "writer": {"SELECT", "INSERT", "UPDATE", "DELETE"}},
    "integration": {"reader": {"SELECT"}, "writer": {"SELECT"}},
}
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


@unittest.skipUnless(
    all(os.environ.get(name) for name in REQUIRED_ENVIRONMENT),
    "isolated PostgreSQL role-grant test environment is not configured",
)
class CoreAssetRoleGrantIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_url = os.environ[ADMIN_URL_ENV]
        cls.reader_url = os.environ[READER_URL_ENV]
        cls.writer_url = os.environ[WRITER_URL_ENV]
        cls.reader_role = os.environ[READER_ROLE_ENV]
        cls.writer_role = os.environ[WRITER_ROLE_ENV]
        with psycopg.connect(cls.admin_url, autocommit=True) as connection:
            for schema_name in TABLE_POLICY:
                connection.execute(
                    f"CREATE TABLE {schema_name}.role_grant_future_{schema_name} "
                    "(record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), value text)"
                )
            connection.execute(
                "CREATE SEQUENCE asset.role_grant_future_sequence"
            )
            connection.execute(
                """
                CREATE FUNCTION asset.role_grant_future_function()
                RETURNS integer LANGUAGE sql AS 'SELECT 1'
                """
            )

    def assert_statement_denied(self, connection, statement):
        with self.assertRaises(psycopg.Error):
            connection.execute(statement)

    def test_role_attributes_and_database_privileges_are_restricted(self):
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            for role_name, expected_read_only in (
                (self.reader_role, "default_transaction_read_only=on"),
                (self.writer_role, "default_transaction_read_only=off"),
            ):
                row = connection.execute(
                    """
                    SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
                           rolreplication, rolbypassrls, coalesce(rolconfig, '{}')
                    FROM pg_roles WHERE rolname = %s
                    """,
                    (role_name,),
                ).fetchone()
                self.assertEqual(row[:7], (True, False, False, False, False, False, False))
                self.assertIn(expected_read_only, row[7])
                self.assertFalse(
                    connection.execute(
                        "SELECT has_database_privilege(%s, current_database(), 'CREATE')",
                        (role_name,),
                    ).fetchone()[0]
                )
                self.assertFalse(
                    connection.execute(
                        "SELECT has_database_privilege(%s, current_database(), 'TEMPORARY')",
                        (role_name,),
                    ).fetchone()[0]
                )

    def test_current_and_future_table_privileges_match_exact_policy(self):
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            relations = connection.execute(
                """
                SELECT namespace.nspname, class.relname, class.oid
                FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = ANY(%s)
                  AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
                ORDER BY namespace.nspname, class.relname
                """,
                (list(TABLE_POLICY),),
            ).fetchall()
            self.assertGreater(len(relations), 20)
            self.assertTrue(any(name.startswith("role_grant_future_") for _, name, _ in relations))
            for schema_name, _, relation_oid in relations:
                for role_kind, role_name in (
                    ("reader", self.reader_role), ("writer", self.writer_role),
                ):
                    actual = {
                        privilege
                        for privilege in TABLE_PRIVILEGES
                        if connection.execute(
                            "SELECT has_table_privilege(%s, %s, %s)",
                            (role_name, relation_oid, privilege),
                        ).fetchone()[0]
                    }
                    self.assertEqual(actual, TABLE_POLICY[schema_name][role_kind])

            for role_name in (self.reader_role, self.writer_role):
                self.assertFalse(
                    connection.execute(
                        "SELECT has_sequence_privilege(%s, 'asset.role_grant_future_sequence', 'USAGE')",
                        (role_name,),
                    ).fetchone()[0]
                )
                self.assertFalse(
                    connection.execute(
                        "SELECT has_function_privilege(%s, 'asset.role_grant_future_function()', 'EXECUTE')",
                        (role_name,),
                    ).fetchone()[0]
                )

    def test_reader_is_read_only_and_cannot_create_temporary_objects(self):
        with psycopg.connect(self.reader_url, autocommit=True) as connection:
            self.assertEqual(
                connection.execute("SHOW default_transaction_read_only").fetchone()[0],
                "on",
            )
            connection.execute("SELECT count(*) FROM asset.environment").fetchone()
            connection.execute("SELECT count(*) FROM ingest.import_batch").fetchone()
            connection.execute("SELECT count(*) FROM integration.observability_gateway_v1").fetchone()
            connection.execute("SELECT count(*) FROM integration.observability_gateway_v2").fetchone()
            self.assert_statement_denied(
                connection,
                "SELECT encrypted_password FROM security.server_ssh_credential",
            )
            self.assert_statement_denied(
                connection,
                "INSERT INTO asset.environment (environment_code, name, stage) "
                "VALUES ('reader-forbidden', 'reader-forbidden', 'test')",
            )
            self.assert_statement_denied(
                connection,
                "CREATE TEMP TABLE reader_forbidden (value integer)",
            )
            self.assert_statement_denied(
                connection,
                "SELECT * FROM public.agentswatch_schema_migration",
            )

    def test_writer_supports_api_and_import_dml_but_not_delete_or_ddl(self):
        environment_id = str(uuid4())
        with psycopg.connect(self.writer_url, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO asset.environment (
                    environment_id, environment_code, name, stage, lifecycle_status
                ) VALUES (%s, %s, %s, 'test', 'planned')
                """,
                (environment_id, f"role-grant-{environment_id}", "role grant integration"),
            )
            connection.execute(
                "UPDATE asset.environment SET name = 'role grant updated' WHERE environment_id = %s",
                (environment_id,),
            )
            connection.execute(
                """
                INSERT INTO audit.change_log (
                    actor_type, actor_ref, source, object_type, object_id, operation
                ) VALUES ('system', 'role-grant-test', 'system', 'Environment', %s, 'create')
                """,
                (environment_id,),
            )
            connection.execute(
                "INSERT INTO asset.role_grant_future_asset (value) VALUES ('allowed')"
            )
            connection.execute(
                "INSERT INTO org.role_grant_future_org (value) VALUES ('allowed')"
            )
            connection.execute(
                "UPDATE org.role_grant_future_org SET value = 'updated' WHERE value = 'allowed'"
            )
            security_user_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO security.user_account (
                    user_id, username, display_name, password_hash, role, created_by, updated_by
                ) VALUES (%s, %s, 'Role Test', 'scrypt$test', 'viewer', 'test', 'test')
                """,
                (security_user_id, f"role-test-{security_user_id}"),
            )
            connection.execute(
                "UPDATE security.user_account SET display_name = 'Role Test Updated' WHERE user_id = %s",
                (security_user_id,),
            )
            connection.execute(
                "DELETE FROM security.user_account WHERE user_id = %s", (security_user_id,)
            )
            import_batch_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO ingest.import_batch (
                    import_batch_id, file_name, file_sha256, template_version, actor_ref
                ) VALUES (%s, 'role-grant.xlsx', %s, 'role-grant-v1', 'role-grant-test')
                """,
                (import_batch_id, "0" * 64),
            )
            connection.execute(
                "UPDATE ingest.import_batch SET status = 'validating' WHERE import_batch_id = %s",
                (import_batch_id,),
            )
            connection.execute(
                "INSERT INTO ingest.role_grant_future_ingest (value) VALUES ('allowed')"
            )
            connection.execute(
                "UPDATE ingest.role_grant_future_ingest SET value = 'updated' WHERE value = 'allowed'"
            )

            for statement in (
                "DELETE FROM ingest.role_grant_future_ingest WHERE value = 'updated'",
                "INSERT INTO integration.role_grant_future_integration (value) VALUES ('forbidden')",
                "DELETE FROM org.role_grant_future_org WHERE value = 'updated'",
                f"DELETE FROM asset.environment WHERE environment_id = '{environment_id}'",
                "CREATE TABLE asset.writer_forbidden (value integer)",
                "ALTER TABLE asset.environment ADD COLUMN writer_forbidden integer",
                "CREATE SCHEMA writer_forbidden",
                "CREATE ROLE writer_forbidden",
                "CREATE DATABASE writer_forbidden",
                "SELECT * FROM public.agentswatch_schema_migration",
            ):
                self.assert_statement_denied(connection, statement)


if __name__ == "__main__":
    unittest.main()
