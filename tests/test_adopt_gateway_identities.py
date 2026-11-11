import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from agentswatch_observability.control import _load_overlay
from scripts import adopt_gateway_identities as adoption


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def inventory_payload(
    *,
    gateway_id: str = "gateway-001",
    stable_server_id: str = "ECS-001",
    host: str = "10.0.0.1",
    port: int = 16402,
    enabled: bool = True,
) -> dict:
    return {
        "defaults": {"token": "must-not-be-copied"},
        "gateways": [
            {
                "gateway_id": gateway_id,
                "host": host,
                "port": port,
                "enabled": enabled,
                "metadata": {
                    "server_id": stable_server_id,
                    "password": "must-not-be-copied",
                },
            }
        ],
    }


def snapshot() -> dict:
    return {
        "database": {"name": "agentswatch", "server_version_num": "160014"},
        "migrations": [
            {
                "version": "001",
                "filename": "001_core_asset_schema.sql",
                "sha256": "1" * 64,
            },
            {
                "version": "020",
                "filename": "020_observability_gateway_v2.sql",
                "sha256": "2" * 64,
            },
        ],
        "runtimes": [
            {
                "runtime_id": uid(1),
                "server_id": uid(11),
                "family_code": "openclaw",
                "runtime_role": "gateway",
                "monitor_host": "10.0.0.1",
                "monitor_port": 16402,
                "runtime_row_version": 1,
                "server_row_version": 2,
            },
            {
                "runtime_id": uid(2),
                "server_id": uid(12),
                "family_code": "openclaw",
                "runtime_role": "gateway",
                "monitor_host": "10.0.0.2",
                "monitor_port": 16402,
                "runtime_row_version": 3,
                "server_row_version": 4,
            },
        ],
        "server_identifiers": [
            {
                "external_identifier_id": uid(101),
                "identifier_value": "ECS-001",
                "server_id": uid(11),
                "row_version": 1,
            },
            {
                "external_identifier_id": uid(102),
                "identifier_value": "ECS-002",
                "server_id": uid(12),
                "row_version": 1,
            },
        ],
        "gateway_identifiers": [],
        "identifier_occupancy": [
            {
                "external_identifier_id": uid(101),
                "source_system": adoption.SERVER_SOURCE_SYSTEM,
                "identifier_type": adoption.SERVER_IDENTIFIER_TYPE,
                "identifier_value": "ECS-001",
                "target_type": "server",
                "target_id": uid(11),
                "row_version": 1,
                "is_current": True,
            },
            {
                "external_identifier_id": uid(102),
                "source_system": adoption.SERVER_SOURCE_SYSTEM,
                "identifier_type": adoption.SERVER_IDENTIFIER_TYPE,
                "identifier_value": "ECS-002",
                "target_type": "server",
                "target_id": uid(12),
                "row_version": 1,
                "is_current": True,
            },
        ],
    }


class InventoryValidationTest(unittest.TestCase):
    def test_extracts_only_required_non_secret_fields(self):
        items = adoption.normalize_inventory(inventory_payload())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["stable_server_id"], "ECS-001")
        self.assertEqual(items[0]["host"], "10.0.0.1")
        self.assertNotIn("password", json.dumps(items))
        self.assertNotIn("token", json.dumps(items))

    def test_rejects_duplicate_gateway_and_stable_server_ids_canonically(self):
        payload = inventory_payload()
        duplicate = copy.deepcopy(payload["gateways"][0])
        duplicate["gateway_id"] = "GATEWAY-001"
        duplicate["metadata"]["server_id"] = "ECS-002"
        payload["gateways"].append(duplicate)
        with self.assertRaisesRegex(adoption.AdoptionRejected, "duplicate gateway_id"):
            adoption.normalize_inventory(payload)

        payload = inventory_payload()
        duplicate = copy.deepcopy(payload["gateways"][0])
        duplicate["gateway_id"] = "gateway-002"
        duplicate["metadata"]["server_id"] = "ｅｃｓ－００１"
        payload["gateways"].append(duplicate)
        with self.assertRaisesRegex(adoption.AdoptionRejected, "metadata.server_id"):
            adoption.normalize_inventory(payload)

    def test_rejects_missing_identity_invalid_host_port_and_non_boolean_enabled(self):
        invalid_cases = (
            ({"stable_server_id": ""}, "metadata.server_id"),
            ({"host": "runtime.example.invalid"}, "must be an IP address"),
            ({"port": 70000}, "between 1 and 65535"),
            ({"enabled": "true"}, "must be a boolean"),
        )
        for changes, message in invalid_cases:
            with self.subTest(changes=changes):
                payload = inventory_payload(**changes)
                with self.assertRaisesRegex(adoption.AdoptionRejected, message):
                    adoption.normalize_inventory(payload)

    def test_database_url_is_available_only_from_dedicated_environment_variable(self):
        with self.assertRaisesRegex(adoption.AdoptionRejected, adoption.DATABASE_URL_ENV):
            adoption.database_url({})
        self.assertEqual(
            adoption.database_url({adoption.DATABASE_URL_ENV: "postgresql://db/agentswatch"}),
            "postgresql://db/agentswatch",
        )
        option_strings = {
            option
            for action in adoption.parser()._actions
            for option in action.option_strings
        }
        self.assertNotIn("--database-url", option_strings)


class AdoptionResolutionTest(unittest.TestCase):
    def setUp(self):
        self.inventory = adoption.normalize_inventory(inventory_payload())
        self.snapshot = snapshot()

    def test_builds_deterministic_append_plan_and_runtime_keyed_safe_overlay(self):
        result = adoption.resolve_adoption(self.inventory, self.snapshot)
        self.assertEqual(len(result["mappings"]), 1)
        mapping = result["mappings"][0]
        self.assertEqual(mapping["runtime_id"], uid(1))
        self.assertEqual(mapping["server_id"], uid(11))
        self.assertEqual(mapping["action"], "insert")
        self.assertEqual(
            mapping["external_identifier_id"],
            adoption.external_identifier_id("gateway-001"),
        )
        self.assertEqual(
            result["native_scrape_overlay"],
            {
                uid(1): {
                    "native_scrape_enabled": True,
                    "scrape_host": "10.0.0.1",
                    "scrape_port": 16402,
                }
            },
        )
        self.assertEqual(result["unmatched_runtimes"][0]["runtime_id"], uid(2))
        self.assertEqual(
            result["unmatched_runtimes"][0]["reason"],
            "not_present_in_legacy_inventory",
        )

    def test_stable_server_identifier_is_the_only_match_key(self):
        missing = adoption.normalize_inventory(
            inventory_payload(stable_server_id="DOES-NOT-EXIST", host="10.0.0.1")
        )
        with self.assertRaisesRegex(adoption.AdoptionRejected, "resolves to 0 current"):
            adoption.resolve_adoption(missing, self.snapshot)

        # The host points at runtime 2, but the immutable server ID points at
        # runtime 1.  The tool must reject instead of falling back to host.
        mismatch = adoption.normalize_inventory(
            inventory_payload(stable_server_id="ECS-001", host="10.0.0.2")
        )
        with self.assertRaisesRegex(adoption.AdoptionRejected, "host consistency"):
            adoption.resolve_adoption(mismatch, self.snapshot)

    def test_host_and_port_are_fail_closed_consistency_checks(self):
        mismatch = adoption.normalize_inventory(inventory_payload(port=16403))
        with self.assertRaisesRegex(adoption.AdoptionRejected, "port consistency"):
            adoption.resolve_adoption(mismatch, self.snapshot)

        database_missing_host = copy.deepcopy(self.snapshot)
        database_missing_host["runtimes"][0]["monitor_host"] = None
        with self.assertRaisesRegex(adoption.AdoptionRejected, "host consistency"):
            adoption.resolve_adoption(self.inventory, database_missing_host)

    def test_rejects_ambiguous_runtime_and_normalized_server_identifier(self):
        ambiguous_runtime = copy.deepcopy(self.snapshot)
        second = copy.deepcopy(ambiguous_runtime["runtimes"][0])
        second["runtime_id"] = uid(3)
        ambiguous_runtime["runtimes"].append(second)
        with self.assertRaisesRegex(adoption.AdoptionRejected, "2 OpenClaw gateway runtimes"):
            adoption.resolve_adoption(self.inventory, ambiguous_runtime)

        ambiguous_identifier = copy.deepcopy(self.snapshot)
        ambiguous_identifier["server_identifiers"].append(
            {
                "external_identifier_id": uid(103),
                "identifier_value": "ecs-001",
                "server_id": uid(12),
                "row_version": 1,
            }
        )
        with self.assertRaisesRegex(adoption.AdoptionRejected, "resolves to 2 current"):
            adoption.resolve_adoption(self.inventory, ambiguous_identifier)

    def test_rejects_gateway_id_conflicts_on_value_or_runtime(self):
        other_runtime = copy.deepcopy(self.snapshot)
        other_runtime["gateway_identifiers"].append(
            {
                "external_identifier_id": uid(201),
                "source_system": "another_system",
                "identifier_type": "gateway_id",
                "identifier_value": "gateway-001",
                "runtime_id": uid(2),
                "row_version": 1,
            }
        )
        with self.assertRaisesRegex(adoption.AdoptionRejected, "conflicts"):
            adoption.resolve_adoption(self.inventory, other_runtime)

        same_runtime_different_value = copy.deepcopy(self.snapshot)
        same_runtime_different_value["gateway_identifiers"].append(
            {
                "external_identifier_id": uid(202),
                "source_system": "another_system",
                "identifier_type": "gateway_id",
                "identifier_value": "different-gateway",
                "runtime_id": uid(1),
                "row_version": 1,
            }
        )
        with self.assertRaisesRegex(adoption.AdoptionRejected, "already has conflicting"):
            adoption.resolve_adoption(self.inventory, same_runtime_different_value)

    def test_exact_tool_owned_row_is_idempotent_and_does_not_drift_baseline(self):
        before = adoption.resolve_adoption(self.inventory, self.snapshot)
        after_snapshot = copy.deepcopy(self.snapshot)
        after_snapshot["gateway_identifiers"].append(
            {
                "external_identifier_id": adoption.external_identifier_id("gateway-001"),
                "source_system": adoption.SOURCE_SYSTEM,
                "identifier_type": "gateway_id",
                "identifier_value": "gateway-001",
                "runtime_id": uid(1),
                "row_version": 1,
            }
        )
        after_snapshot["identifier_occupancy"].append(
            {
                "external_identifier_id": adoption.external_identifier_id("gateway-001"),
                "source_system": adoption.SOURCE_SYSTEM,
                "identifier_type": "gateway_id",
                "identifier_value": "gateway-001",
                "target_type": "runtime",
                "target_id": uid(1),
                "row_version": 1,
                "is_current": True,
            }
        )
        after = adoption.resolve_adoption(self.inventory, after_snapshot)
        self.assertEqual(after["mappings"][0]["action"], "reuse")
        self.assertEqual(before["database_fingerprint"], after["database_fingerprint"])
        self.assertEqual(
            adoption._semantic_mappings(before["mappings"]),
            adoption._semantic_mappings(after["mappings"]),
        )
        # A planned insert may become an exact, receipted reuse after a prior
        # successful replay.  The inverse transition means a row disappeared
        # after planning and must fail closed instead of being repaired.
        adoption._validate_action_transitions(before["mappings"], after["mappings"])
        with self.assertRaisesRegex(adoption.AdoptionRejected, "disappeared"):
            adoption._validate_action_transitions(after["mappings"], before["mappings"])

    def test_database_row_drift_changes_fingerprint(self):
        before = adoption.resolve_adoption(self.inventory, self.snapshot)
        changed = copy.deepcopy(self.snapshot)
        changed["runtimes"][1]["runtime_row_version"] += 1
        after = adoption.resolve_adoption(self.inventory, changed)
        self.assertNotEqual(before["database_fingerprint"], after["database_fingerprint"])

        changed = copy.deepcopy(self.snapshot)
        changed["migrations"][1]["sha256"] = "3" * 64
        after = adoption.resolve_adoption(self.inventory, changed)
        self.assertNotEqual(before["database_fingerprint"], after["database_fingerprint"])

    def test_rejects_deterministic_uuid_occupied_by_any_identifier_type(self):
        occupied = copy.deepcopy(self.snapshot)
        occupied["identifier_occupancy"].append(
            {
                "external_identifier_id": adoption.external_identifier_id("gateway-001"),
                "source_system": "employee_inventory",
                "identifier_type": "employee_no",
                "identifier_value": "E0001",
                "target_type": "person",
                "target_id": uid(501),
                "row_version": 1,
                "is_current": True,
            }
        )
        with self.assertRaisesRegex(adoption.AdoptionRejected, "UUID is already occupied"):
            adoption.resolve_adoption(self.inventory, occupied)

    def test_rejects_postgresql_older_than_16(self):
        old = copy.deepcopy(self.snapshot)
        old["database"]["server_version_num"] = "150012"
        with self.assertRaisesRegex(adoption.AdoptionRejected, "PostgreSQL 16"):
            adoption.resolve_adoption(self.inventory, old)


class PlanArtifactTest(unittest.TestCase):
    def setUp(self):
        self.inventory = adoption.normalize_inventory(inventory_payload())
        self.plan = adoption.build_plan(
            inventory_path=Path("cluster/lobsters.json"),
            inventory_sha256="a" * 64,
            inventory=self.inventory,
            snapshot=snapshot(),
            observed_at="2026-07-17T00:00:00Z",
        )

    def test_plan_contains_required_fingerprints_mapping_overlay_and_no_secret(self):
        self.assertRegex(self.plan["plan_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(self.plan["database"]["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.plan["inventory"]["sha256"], "a" * 64)
        self.assertIn(uid(1), self.plan["native_scrape_overlay"])
        serialized = adoption.canonical_json(self.plan)
        self.assertNotIn("must-not-be-copied", serialized)
        self.assertNotIn("postgresql://", serialized)

    def test_plan_hash_detects_semantic_tampering(self):
        adoption._validate_plan_document(self.plan)
        tampered = copy.deepcopy(self.plan)
        tampered["mappings"][0]["gateway_id"] = "gateway-evil"
        with self.assertRaisesRegex(adoption.AdoptionRejected, "plan_hash"):
            adoption._validate_plan_document(tampered)

    def test_exact_file_sha_confirmation_detects_even_whitespace_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            expected = adoption._atomic_write_json(path, self.plan)
            loaded, actual = adoption.load_confirmed_plan(path, expected)
            self.assertEqual(loaded, self.plan)
            self.assertEqual(actual, expected)
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(adoption.AdoptionRejected, "file SHA-256 mismatch"):
                adoption.load_confirmed_plan(path, expected)

    def test_independent_overlay_file_is_directly_consumable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-overlay.json"
            adoption._atomic_write_json(path, self.plan["native_scrape_overlay"])
            loaded = _load_overlay(path)
            self.assertEqual(loaded, self.plan["native_scrape_overlay"])
            self.assertEqual(
                set(loaded[uid(1)]),
                {"native_scrape_enabled", "scrape_host", "scrape_port"},
            )

    def test_duplicate_json_object_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"gateways": [], "gateways": []}', encoding="utf-8")
            with self.assertRaisesRegex(adoption.AdoptionRejected, "duplicate object key"):
                adoption._load_json(path, maximum_bytes=1024, label="inventory")


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeReceiptConnection:
    def __init__(self, row):
        self.row = row

    def execute(self, _query, _parameters=()):
        return FakeResult(self.row)


class AuditReceiptTest(unittest.TestCase):
    def setUp(self):
        item = adoption.normalize_inventory(inventory_payload())
        resolved = adoption.resolve_adoption(item, snapshot())
        self.mapping = copy.deepcopy(resolved["mappings"][0])
        self.mapping["action"] = "reuse"

    def test_exact_reuse_requires_immutable_adoption_receipt(self):
        with self.assertRaisesRegex(adoption.AdoptionRejected, "no valid immutable"):
            adoption.validate_reuse_receipts(
                FakeReceiptConnection(None), [self.mapping]
            )

        identifier_id = self.mapping["external_identifier_id"]
        receipt = {
            "change_id": adoption.change_id(identifier_id),
            "actor_type": "migration",
            "actor_ref": "production-deployment",
            "source": "migration",
            "object_type": "external_identifier",
            "object_id": identifier_id,
            "operation": "link",
            "after_value": {
                "source_system": adoption.SOURCE_SYSTEM,
                "identifier_type": "gateway_id",
                "identifier_value": self.mapping["gateway_id"],
                "runtime_id": self.mapping["runtime_id"],
            },
        }
        adoption.validate_reuse_receipts(
            FakeReceiptConnection(receipt), [self.mapping]
        )


if __name__ == "__main__":
    unittest.main()
