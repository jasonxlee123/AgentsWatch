import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


simulator = load_module(
    "simulate_asset_import_contract",
    ROOT / "scripts" / "simulate_asset_import_contract.py",
)


class AssetImportContractSimulationTests(unittest.TestCase):
    def setUp(self):
        self.current = [
            {
                "platform_id": "00000000-0000-4000-8000-000000000401",
                "external_code": "ECS-001",
                "row_version": 7,
                "name": "生产服务器 A",
                "internal_ip": "10.0.0.10",
                "public_ip": "203.0.113.10",
                "lifecycle_status": "active",
            }
        ]
        self.options = {
            "mutable_fields": ("name", "internal_ip", "public_ip", "lifecycle_status"),
            "nullable_fields": ("public_ip",),
        }

    def plan(self, rows, **overrides):
        options = dict(self.options)
        options.update(overrides)
        return simulator.plan_import_batch(self.current, rows, **options)

    def update_row(self, **values):
        row = {
            "platform_id": self.current[0]["platform_id"],
            "external_code": self.current[0]["external_code"],
            "row_version": 7,
        }
        row.update(values)
        return row

    def test_new_object_is_classified_without_name_matching(self):
        result = self.plan(
            [
                {
                    "external_code": "ECS-002",
                    "name": "生产服务器 A",
                    "internal_ip": "10.0.0.11",
                    "lifecycle_status": "planned",
                }
            ]
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["rows"][0]["status"], "new")

    def test_duplicate_rows_are_rejected(self):
        row = self.update_row(name="新名称")
        result = self.plan([row, dict(row)])

        self.assertFalse(result["ready"])
        self.assertTrue(all(item["issues"][0]["code"] == "DUPLICATE_IMPORT_ROW" for item in result["rows"]))

    def test_rename_and_ip_change_are_field_level_sets(self):
        result = self.plan([self.update_row(name="生产服务器 B", internal_ip="10.0.0.20")])
        actions = {
            item["field"]: item["action"]
            for item in result["rows"][0]["changes"]
        }

        self.assertEqual(result["rows"][0]["status"], "changed")
        self.assertEqual(actions["name"], "set")
        self.assertEqual(actions["internal_ip"], "set")

    def test_empty_cells_mean_ignore(self):
        result = self.plan([self.update_row(name="", internal_ip=None, public_ip="")])

        self.assertEqual(result["rows"][0]["status"], "unchanged")
        self.assertTrue(all(item["action"] == "ignore" for item in result["rows"][0]["changes"]))

    def test_clear_marker_clears_only_nullable_field(self):
        result = self.plan([self.update_row(public_ip=simulator.CLEAR_MARKER)])

        change = next(item for item in result["rows"][0]["changes"] if item["field"] == "public_ip")
        self.assertEqual(change["action"], "clear")
        self.assertIsNone(change["after"])

    def test_clear_marker_on_required_field_is_rejected(self):
        result = self.plan([self.update_row(name=simulator.CLEAR_MARKER)])

        self.assertFalse(result["ready"])
        self.assertEqual(result["rows"][0]["issues"][0]["code"], "FIELD_NOT_CLEARABLE")

    def test_concurrent_update_rejects_whole_batch(self):
        result = self.plan([self.update_row(row_version=6, name="过期修改")])

        self.assertFalse(result["ready"])
        self.assertEqual(result["rows"][0]["status"], "conflict")
        self.assertEqual(result["rows"][0]["issues"][0]["code"], "ROW_VERSION_CONFLICT")

    def test_retirement_is_an_explicit_state_change(self):
        result = self.plan([self.update_row(lifecycle_status="retired")])

        change = next(item for item in result["rows"][0]["changes"] if item["field"] == "lifecycle_status")
        self.assertEqual(change, {"field": "lifecycle_status", "action": "set", "before": "active", "after": "retired"})

    def test_external_code_can_match_existing_only_in_initial_migration(self):
        row = {"external_code": "ECS-001", "name": "生产服务器 A", "row_version": 7}
        standard = self.plan([row])
        initial = self.plan([row], import_mode="initial_migration")

        self.assertEqual(standard["rows"][0]["issues"][0]["code"], "PLATFORM_ID_REQUIRED_FOR_UPDATE")
        self.assertTrue(initial["ready"])
        self.assertEqual(initial["rows"][0]["platform_id"], self.current[0]["platform_id"])


if __name__ == "__main__":
    unittest.main()
