from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID

from agentswatch_api.confirmation import (
    CONFIRMATION_HEADER,
    ConfirmationRejected,
    ConfirmationRequired,
    ConfirmationService,
    fingerprint_request,
    format_confirmation_id,
    parse_confirmation_id,
    request_component_hash,
)


USER_ID = UUID("10000000-0000-4000-8000-000000000001")
CONFIRMATION_ID = UUID("20000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class StubConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return Result(self.rows.pop(0))


class StubDatabase:
    def __init__(self, rows):
        self.connection_value = StubConnection(rows)

    @contextmanager
    def connection(self):
        yield self.connection_value


def confirmation_row(*, used_at=None):
    fingerprint = fingerprint_request(
        "POST", "/api/v1/automation/runs", "limit=10", {"target_ids": ["one"]},
    )
    return {
        "confirmation_id": CONFIRMATION_ID,
        "user_id": USER_ID,
        "request_method": fingerprint.method,
        "request_path": fingerprint.path,
        "query_hash": fingerprint.query_hash,
        "body_hash": fingerprint.body_hash,
        "expires_at": NOW + timedelta(minutes=5),
        "used_at": used_at,
        "created_at": NOW,
    }


class OperationConfirmationTests(unittest.TestCase):
    def test_component_hash_is_canonical_for_json_and_sensitive_to_changes(self):
        first = request_component_hash({"parameters": {"force": True}, "targets": ["a", "b"]})
        reordered = request_component_hash({"targets": ["a", "b"], "parameters": {"force": True}})
        changed = request_component_hash({"targets": ["a"], "parameters": {"force": True}})

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, changed)
        self.assertNotEqual(request_component_hash("null"), request_component_hash(None))

    def test_public_id_is_namespaced_and_plain_or_malformed_ids_are_rejected(self):
        public_id = format_confirmation_id(CONFIRMATION_ID)

        self.assertEqual(public_id, "cfm_20000000000040008000000000000001")
        self.assertEqual(parse_confirmation_id(public_id), CONFIRMATION_ID)
        with self.assertRaises(ConfirmationRejected):
            parse_confirmation_id(str(CONFIRMATION_ID))
        with self.assertRaises(ConfirmationRejected):
            parse_confirmation_id("cfm_not-a-uuid")

    def test_prepare_persists_only_hashes_and_returns_the_public_challenge(self):
        database = StubDatabase([confirmation_row()])
        service = ConfirmationService(database=database, ttl_seconds=120)

        result = service.prepare(
            USER_ID,
            "post",
            "/api/v1/automation/runs",
            "limit=10",
            {"target_ids": ["one"]},
        )

        sql, params = database.connection_value.calls[0]
        self.assertIn("INSERT INTO security.operation_confirmation", sql)
        self.assertEqual(params["user_id"], USER_ID)
        self.assertEqual(params["request_method"], "POST")
        self.assertEqual(params["request_path"], "/api/v1/automation/runs")
        self.assertEqual(params["ttl"], timedelta(seconds=120))
        self.assertNotIn("target_ids", str(params))
        self.assertEqual(result.public_id, format_confirmation_id(CONFIRMATION_ID))

    def test_verify_binds_every_request_component_and_rejects_a_mismatch(self):
        database = StubDatabase([confirmation_row(), None])
        service = ConfirmationService(database=database)
        public_id = format_confirmation_id(CONFIRMATION_ID)

        verified = service.verify(
            public_id,
            USER_ID,
            "POST",
            "/api/v1/automation/runs",
            "limit=10",
            {"target_ids": ["one"]},
        )
        with self.assertRaises(ConfirmationRejected):
            service.verify(
                public_id,
                USER_ID,
                "POST",
                "/api/v1/automation/runs",
                "limit=10",
                {"target_ids": ["changed"]},
            )

        sql, params = database.connection_value.calls[0]
        self.assertEqual(verified.confirmation_id, CONFIRMATION_ID)
        for binding in (
            "confirmation_id", "user_id", "request_method", "request_path", "query_hash", "body_hash",
        ):
            self.assertIn(f"{binding} = %({binding})s", sql)
        self.assertIn("used_at IS NULL", sql)
        self.assertIn("expires_at > now()", sql)
        self.assertNotEqual(
            params["body_hash"], database.connection_value.calls[1][1]["body_hash"],
        )

    def test_consume_is_one_atomic_conditional_update(self):
        used = NOW + timedelta(seconds=30)
        database = StubDatabase([confirmation_row(used_at=used), None])
        service = ConfirmationService(database=database)
        arguments = (
            format_confirmation_id(CONFIRMATION_ID),
            USER_ID,
            "POST",
            "/api/v1/automation/runs",
            "limit=10",
            {"target_ids": ["one"]},
        )

        consumed = service.consume(*arguments)
        with self.assertRaises(ConfirmationRejected):
            service.consume(*arguments)

        sql, _ = database.connection_value.calls[0]
        self.assertIn("UPDATE security.operation_confirmation", sql)
        self.assertIn("SET used_at = now()", sql)
        self.assertIn("used_at IS NULL", sql)
        self.assertIn("expires_at > now()", sql)
        self.assertEqual(consumed.used_at, used)

    def test_require_prepares_a_409_ready_detail_then_consumes_on_retry(self):
        database = StubDatabase([confirmation_row(), confirmation_row(used_at=NOW + timedelta(seconds=10))])
        service = ConfirmationService(database=database)
        request = {
            "user_id": USER_ID,
            "method": "POST",
            "path": "/api/v1/automation/runs",
            "query": "limit=10",
            "body": {"target_ids": ["one"]},
        }

        with self.assertRaises(ConfirmationRequired) as caught:
            service.require(None, **request)
        detail = caught.exception.as_detail()
        consumed = service.require(detail["confirmation_id"], **request)

        self.assertEqual(detail["code"], "CONFIRMATION_REQUIRED")
        self.assertEqual(detail["confirmation_header"], CONFIRMATION_HEADER)
        self.assertEqual(consumed.confirmation_id, CONFIRMATION_ID)
        self.assertEqual(len(database.connection_value.calls), 2)

    def test_invalid_ttl_and_request_shape_fail_closed_before_database_use(self):
        with self.assertRaises(ValueError):
            ConfirmationService(database=StubDatabase([]), ttl_seconds=0)
        with self.assertRaises(ValueError):
            fingerprint_request("POST", "api/v1/unsafe")
        with self.assertRaises(ValueError):
            fingerprint_request("P0ST", "/api/v1/unsafe")


if __name__ == "__main__":
    unittest.main()
