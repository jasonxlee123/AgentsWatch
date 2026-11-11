import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "core_asset_import_sample.xlsx"
CONFIG = ROOT / "config" / "asset_import_template_v2.json"
MASTER = ROOT / "tests" / "fixtures" / "asset_import_master.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "scripts"))
preview = load_module("preview_asset_import", ROOT / "scripts" / "preview_asset_import.py")


class AssetImportPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG.read_text())
        cls.definitions = {item["name"]: item for item in cls.config["sheets"]}
        cls.master = json.loads(MASTER.read_text())

    def parsed(self, row_number, **values):
        return {"row_number": row_number, "values": values, "formula_fields": set()}

    def plan(self, sheet_name, rows, import_mode="standard"):
        return preview.validate_and_plan_sheet(
            self.definitions[sheet_name],
            rows,
            self.master.get(sheet_name, []),
            import_mode,
        )

    def test_generated_empty_template_is_structurally_valid(self):
        workbook = Workbook()
        metadata = workbook.active
        metadata.title = "_Meta"
        metadata.append(["key", "value"])
        metadata.append(["agentswatch_template_version", self.config["template_version"]])
        for definition in self.config["sheets"]:
            sheet = workbook.create_sheet(definition["name"])
            sheet.append(definition["headers"])

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "core-asset-import-template-v2.xlsx"
            workbook.save(template)
            workbook.close()
            report = preview.preview_workbook(template, CONFIG)

        self.assertTrue(report["ready"], report["issues"])
        self.assertEqual(report["template_version"], "2.0")
        self.assertEqual(report["summary"]["sheets"], 13)
        self.assertEqual(report["summary"]["rows"], 0)

    def test_sample_workbook_runs_end_to_end_through_xlsx_parser(self):
        report = preview.preview_workbook(SAMPLE, CONFIG, self.master)

        self.assertTrue(report["ready"], report["rows"])
        self.assertEqual(report["summary"]["rows"], 5)
        self.assertEqual(report["summary"]["status_counts"], {"changed": 1, "new": 4})
        self.assertEqual(len(report["staging"]["import_rows"]), 5)
        self.assertEqual(len(report["staging"]["import_issues"]), 0)
        self.assertGreater(len(report["staging"]["import_changes"]), 20)
        self.assertEqual(report["staging"]["import_batch"]["status"], "ready")

    def test_server_rename_ip_change_and_empty_cells_generate_field_differences(self):
        reports = self.plan(
            "Servers",
            [
                self.parsed(
                    2,
                    platform_id="00000000-0000-4000-8000-000000000501",
                    row_version=7,
                    environment_code=None,
                    provider_code=None,
                    provider_instance_id="ECS-001",
                    name="生产服务器 B",
                    lifecycle_status=None,
                    cpu_cores=None,
                    memory_gib=None,
                    disk_gib=None,
                    internal_ip="10.0.0.20",
                    public_ip=None,
                    os_name=None,
                    os_version=None,
                    architecture=None,
                    work_account=None,
                )
            ],
        )

        self.assertEqual(reports[0]["status"], "changed")
        actions = {item["field"]: item["action"] for item in reports[0]["changes"]}
        self.assertEqual(actions["name"], "set")
        self.assertEqual(actions["internal_ip"], "set")
        self.assertEqual(actions["public_ip"], "ignore")

    def test_clear_marker_and_row_version_conflict_are_reported(self):
        clear = self.plan(
            "Servers",
            [self.parsed(2, platform_id="00000000-0000-4000-8000-000000000501", row_version=7, public_ip="__CLEAR__")],
        )[0]
        conflict = self.plan(
            "Servers",
            [self.parsed(3, platform_id="00000000-0000-4000-8000-000000000501", row_version=6, name="过期改名")],
        )[0]

        self.assertEqual(next(item for item in clear["changes"] if item["field"] == "public_ip")["action"], "clear")
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["issues"][0]["code"], "ROW_VERSION_CONFLICT")

    def test_external_code_matches_existing_only_during_initial_migration(self):
        row = self.parsed(
            2,
            platform_id=None,
            row_version=7,
            provider_instance_id="ECS-001",
            name="生产服务器 A",
            lifecycle_status="active",
            environment_code="prod",
        )

        standard = self.plan("Servers", [row], "standard")[0]
        initial = self.plan("Servers", [row], "initial_migration")[0]

        self.assertEqual(standard["issues"][0]["code"], "PLATFORM_ID_REQUIRED_FOR_UPDATE")
        self.assertFalse(initial["issues"])
        self.assertEqual(initial["platform_id"], "00000000-0000-4000-8000-000000000501")

    def test_agent_and_runtime_conditional_fields_are_validated(self):
        agent = self.plan(
            "Agents",
            [
                self.parsed(
                    2,
                    name="缺失服务人员",
                    structure_role="primary",
                    service_archetype="personal",
                    lifecycle_status="active",
                    business_admin_employee_no="E001",
                    it_admin_employee_no="E002",
                )
            ],
        )[0]
        runtime = self.plan(
            "Runtimes",
            [
                self.parsed(
                    2,
                    runtime_family_code="openclaw",
                    name="不完整容器",
                    runtime_role="gateway",
                    deployment_mode="container",
                    lifecycle_status="planned",
                    server_platform_id="00000000-0000-4000-8000-000000000501",
                )
            ],
        )[0]

        self.assertIn("SERVICE_ARCHETYPE_FIELD_REQUIRED", {item["code"] for item in agent["issues"]})
        self.assertIn("RUNTIME_MODE_FIELDS_INVALID", {item["code"] for item in runtime["issues"]})

    def test_secret_content_is_redacted_and_rejected(self):
        report = self.plan(
            "Servers",
            [
                self.parsed(
                    2,
                    environment_code="prod",
                    provider_instance_id="ECS-NEW",
                    name="含凭据服务器",
                    lifecycle_status="planned",
                    work_account="password=do-not-store",
                )
            ],
        )[0]

        self.assertEqual(report["status"], "invalid")
        self.assertIn("SECRET_CONTENT_DETECTED", {item["code"] for item in report["issues"]})
        self.assertEqual(report["normalized_payload"]["work_account"], "[REDACTED]")

    def test_new_relationship_does_not_require_its_own_external_code(self):
        report = self.plan(
            "AgentAccessUsers",
            [
                self.parsed(
                    2,
                    agent_platform_id="00000000-0000-4000-8000-000000000901",
                    employee_no="E003",
                    valid_from="2026-07-12T00:00:00+08:00",
                )
            ],
        )[0]

        self.assertEqual(report["status"], "new")
        self.assertFalse(report["issues"])


if __name__ == "__main__":
    unittest.main()
