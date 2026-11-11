from __future__ import annotations

import re
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from agentswatch_api.auth import (
    API_KEY_PATTERN,
    AuthService,
    AuthenticationError,
    digest,
    generate_api_key,
)


USER_ID = UUID("00000000-0000-4000-8000-000000000901")
KEY_ID = UUID("00000000-0000-4000-8000-000000000902")
NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class ScriptedConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        row = self.rows.pop(0) if self.rows else None
        return Result(row)


class ScriptedDatabase:
    def __init__(self, rows):
        self.connection_instance = ScriptedConnection(rows)

    @contextmanager
    def connection(self):
        yield self.connection_instance


def account(*, status="active"):
    return {
        "user_id": USER_ID,
        "username": "operator",
        "display_name": "Operator",
        "role": "operator",
        "status": status,
        "last_login_at": None,
        "row_version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def key_row(*, prefix="awk_0123456789abcdef", revoked_at=None):
    return {
        "api_key_id": KEY_ID,
        "key_prefix": prefix,
        "created_at": NOW,
        "last_used_at": None,
        "revoked_at": revoked_at,
    }


class ApiKeyAuthenticationTests(unittest.TestCase):
    def test_generated_key_has_a_public_identifier_and_256_bit_random_secret(self):
        first, first_prefix = generate_api_key()
        second, second_prefix = generate_api_key()

        self.assertRegex(first_prefix, r"^awk_[0-9a-f]{16}$")
        self.assertTrue(API_KEY_PATTERN.fullmatch(first))
        self.assertTrue(first.startswith(first_prefix + "_"))
        self.assertEqual(len(first[len(first_prefix) + 1:]), 43)
        self.assertNotEqual((first, first_prefix), (second, second_prefix))

    def test_create_returns_secret_once_and_persists_only_hash_and_prefix(self):
        database = ScriptedDatabase([account(), None, key_row()])
        service = AuthService(database)
        token = "awk_0123456789abcdef_" + "A" * 43

        with patch(
            "agentswatch_api.auth.generate_api_key",
            return_value=(token, "awk_0123456789abcdef"),
        ):
            created = service.create_api_key(USER_ID)

        insert_sql, insert_parameters = database.connection_instance.calls[-1]
        self.assertIn("INSERT INTO security.api_key", insert_sql)
        self.assertEqual(insert_parameters[0], USER_ID)
        self.assertEqual(insert_parameters[1], created["prefix"])
        self.assertEqual(insert_parameters[2], digest(token))
        self.assertNotIn(token, repr(insert_parameters))
        self.assertNotIn("key_hash", created)
        self.assertNotIn("secret", created)

    def test_create_rejects_an_existing_key_and_a_disabled_user(self):
        existing_database = ScriptedDatabase([account(), key_row()])
        with self.assertRaisesRegex(ValueError, "已存在"):
            AuthService(existing_database).create_api_key(USER_ID)
        self.assertEqual(len(existing_database.connection_instance.calls), 2)

        disabled_database = ScriptedDatabase([account(status="disabled")])
        with self.assertRaisesRegex(AuthenticationError, "已停用"):
            AuthService(disabled_database).create_api_key(USER_ID)
        self.assertEqual(len(disabled_database.connection_instance.calls), 1)

    def test_rotate_revokes_old_key_before_inserting_a_new_hash(self):
        database = ScriptedDatabase([account(), {"api_key_id": KEY_ID}, key_row()])

        rotated = AuthService(database).rotate_api_key(USER_ID)

        calls = database.connection_instance.calls
        self.assertIn("SET revoked_at = now()", calls[1][0])
        self.assertIn("INSERT INTO security.api_key", calls[2][0])
        self.assertEqual(calls[2][1][2], digest(rotated["api_key"]))

    def test_rotate_requires_an_existing_active_key(self):
        database = ScriptedDatabase([account(), None])

        with self.assertRaisesRegex(ValueError, "不存在"):
            AuthService(database).rotate_api_key(USER_ID)

        self.assertEqual(len(database.connection_instance.calls), 2)

    def test_status_and_revoke_do_not_expose_historical_secret_material(self):
        status_database = ScriptedDatabase([account(), key_row()])
        status = AuthService(status_database).get_api_key_status(USER_ID)
        self.assertEqual(
            status,
            {
                "active": True,
                "api_key_id": str(KEY_ID),
                "prefix": "awk_0123456789abcdef",
                "created_at": NOW,
                "last_used_at": None,
            },
        )

        revoke_database = ScriptedDatabase([account(), None])
        revoked = AuthService(revoke_database).revoke_api_key(USER_ID)
        self.assertFalse(revoked["active"])
        self.assertIsNone(revoked["prefix"])
        self.assertIn("revoked_at IS NULL", revoke_database.connection_instance.calls[-1][0])

    def test_authentication_updates_last_used_and_returns_normal_user_permissions(self):
        token, prefix = generate_api_key()
        authenticated_row = account() | {"api_key_id": KEY_ID, "key_prefix": prefix}
        database = ScriptedDatabase([authenticated_row, None])

        user = AuthService(database).authenticate_api_key(token)

        query, parameters = database.connection_instance.calls[0]
        self.assertIn("account.status = 'active'", query)
        self.assertEqual(parameters, (digest(token),))
        self.assertIn("SET last_used_at = now()", database.connection_instance.calls[1][0])
        self.assertEqual(user["authentication_method"], "api_key")
        self.assertEqual(user["api_key_id"], str(KEY_ID))
        self.assertIn("execute_automation", user["permissions"])
        self.assertNotIn("key_hash", user)

    def test_malformed_revoked_or_disabled_credentials_are_rejected_generically(self):
        malformed_database = ScriptedDatabase([])
        with self.assertRaisesRegex(AuthenticationError, "无效或已撤销"):
            AuthService(malformed_database).authenticate_api_key("not-an-api-key")
        self.assertEqual(malformed_database.connection_instance.calls, [])

        missing_database = ScriptedDatabase([None])
        valid_token, _ = generate_api_key()
        with self.assertRaisesRegex(AuthenticationError, "无效或已撤销"):
            AuthService(missing_database).authenticate_api_key(valid_token)
        self.assertIn("account.status = 'active'", missing_database.connection_instance.calls[0][0])

    def test_disabling_a_user_permanently_revokes_sessions_and_api_key(self):
        before = account() | {
            "password_hash": "scrypt$test",
            "password_changed_at": NOW,
        }
        after = before | {"status": "disabled", "row_version": 2}
        database = ScriptedDatabase([before, after, None, None])

        updated = AuthService(database).update_user(
            USER_ID, 1, {"status": "disabled"}, "administrator",
        )

        self.assertEqual(updated["status"], "disabled")
        statements = [statement for statement, _ in database.connection_instance.calls]
        self.assertTrue(any("UPDATE security.user_session" in item for item in statements))
        self.assertTrue(any("UPDATE security.api_key SET revoked_at" in item for item in statements))

    def test_migration_stores_hash_and_enforces_one_active_key_per_user(self):
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[1]
            / "database"
            / "core-assets"
            / "018_api_keys.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("key_hash char(64) NOT NULL UNIQUE", migration)
        self.assertIn("WHERE revoked_at IS NULL", migration)
        self.assertNotRegex(migration, re.compile(r"\b(secret|plaintext|token)\b", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
