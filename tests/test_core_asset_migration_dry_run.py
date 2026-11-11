import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "cluster" / "lobsters.example.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migration = load_module(
    "dry_run_core_asset_migration",
    ROOT / "scripts" / "dry_run_core_asset_migration.py",
)


class CoreAssetMigrationDryRunTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads(INVENTORY.read_text())

    def test_example_inventory_preserves_expected_object_counts(self):
        report = migration.build_migration_plan(self.source)

        self.assertTrue(report["structurally_mappable"])
        self.assertFalse(report["ready_to_commit"])
        self.assertEqual(report["source_profile"]["rows"], 3)
        self.assertEqual(report["staged_counts"]["servers"], 3)
        self.assertEqual(report["staged_counts"]["runtimes"], 3)
        self.assertEqual(report["staged_counts"]["primary_agents"], 3)
        self.assertEqual(report["staged_counts"]["subagents"], 0)
        self.assertEqual(report["staged_counts"]["agent_deployments"], 3)
        self.assertEqual(report["staged_counts"]["runtime_discovery_snapshots"], 3)
        self.assertEqual(report["staged_counts"]["network_requirement_candidates"], 6)

    def test_generated_platform_ids_are_stable_unique_and_type_separated(self):
        first = migration.build_migration_plan(self.source)
        second = migration.build_migration_plan(copy.deepcopy(self.source))

        self.assertEqual(first["source_hash"], second["source_hash"])
        self.assertEqual(first["samples"], second["samples"])
        self.assertTrue(first["identity_checks"]["all_generated_ids_are_unique"])
        self.assertEqual(first["identity_checks"]["generated_id_count"], 3 * 6)
        sample = first["samples"][0]
        generated = [value for key, value in sample.items() if key.endswith("_id") and not key.startswith("source_")]
        self.assertEqual(len(generated), len(set(generated)))

    def test_unresolved_fields_are_reported_instead_of_inferred(self):
        report = migration.build_migration_plan(self.source)

        self.assertEqual(report["unresolved"]["service_archetypes"], 3)
        self.assertEqual(report["unresolved"]["department_codes"], 3)
        self.assertEqual(report["unresolved"]["administrator_employee_numbers"], 6)
        self.assertEqual(report["unresolved"]["runtime_primary_agent_keys"], 3)
        self.assertNotIn("PLACEHOLDER_VALUE", report["issue_summary"]["by_code"])
        self.assertEqual(report["issue_summary"]["by_code"]["STRUCTURED_SUBAGENTS_UNAVAILABLE"], 2)

    def test_duplicate_legacy_identity_blocks_structural_migration(self):
        self.source["gateways"][1]["gateway_id"] = self.source["gateways"][0]["gateway_id"]

        report = migration.build_migration_plan(self.source)

        self.assertFalse(report["structurally_mappable"])
        self.assertEqual(report["issue_summary"]["by_code"]["SOURCE_IDENTITY_DUPLICATE"], 1)

    def test_mounted_agent_counts_never_create_fake_subagents(self):
        report = migration.build_migration_plan(self.source)

        self.assertEqual(report["source_profile"]["mounted_agent_counts"], {"1": 1, "2": 1, "4": 1})
        self.assertEqual(report["staged_counts"]["subagents"], 0)


if __name__ == "__main__":
    unittest.main()
