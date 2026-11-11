import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

from openpyxl import load_workbook

from agentswatch_api.import_management import BulkImportService
from agentswatch_api.server_management import ConflictError


class BulkImportManagementTests(unittest.TestCase):
    @staticmethod
    def workbook_bytes(configure=None):
        workbook = load_workbook(BytesIO(BulkImportService.template_bytes()))
        if configure:
            configure(workbook)
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()

    @staticmethod
    def empty_state():
        return {
            "department": {}, "person": {}, "server": {}, "runtime": {}, "agent": {},
            "environment": {"prod": "environment-id"}, "provider": {},
            "runtime_family": {"openclaw": "family-id"},
        }

    def test_template_keeps_all_five_data_sheets_empty(self):
        workbook = load_workbook(BytesIO(BulkImportService.template_bytes()), read_only=True)
        self.assertEqual(
            workbook.sheetnames,
            ["说明", "部门", "人员", "服务器", "运行时", "智能体"],
        )
        self.assertEqual(
            {name: workbook[name].max_row for name in workbook.sheetnames[1:]},
            {"部门": 1, "人员": 1, "服务器": 1, "运行时": 1, "智能体": 1},
        )
        workbook.close()

    def test_runtime_import_can_complete_paths_for_fully_unmanaged_active_mapping(self):
        runtime_id = uuid4()
        server_id = uuid4()
        family_id = uuid4()
        before = {
            "runtime_id": runtime_id,
            "server_id": server_id,
            "runtime_family_id": family_id,
            "deployment_mode": "baremetal",
            "lifecycle_status": "active",
            "config_path": None,
            "state_path": None,
        }
        data = {
            "runtime_code": "RT-UNMANAGED",
            "server_code": "SRV-UNMANAGED",
            "runtime_family_code": "openclaw",
            "name": "待纳管运行时",
            "runtime_role": "gateway",
            "deployment_mode": "baremetal",
            "lifecycle_status": "active",
            "config_path": "/home/openclaw/.openclaw/openclaw.json",
            "state_path": "/home/openclaw/.openclaw",
        }
        row = {
            "normalized_payload": data,
            "target_object_id": runtime_id,
            "expected_row_version": 3,
            "changed_fields": ["config_path", "state_path"],
        }
        connection = MagicMock()

        def execute(sql, params=None):
            cursor = MagicMock()
            if "count(member.deployment_member_id)" in sql:
                cursor.fetchone.return_value = {
                    "active_deployment_count": 2,
                    "current_member_count": 2,
                    "all_members_unmanaged": True,
                }
            elif "UPDATE asset.runtime_instance" in sql:
                cursor.rowcount = 1
            return cursor

        connection.execute.side_effect = execute
        service = BulkImportService(database=MagicMock())
        with (
            patch.object(service, "_json", side_effect=[before, {**before, **data}]),
            patch.object(service, "_active_operation", return_value=None),
            patch.object(service, "_identifier"),
            patch.object(service, "_audit"),
        ):
            service._commit_runtime(
                connection, row,
                {"server": {"SRV-UNMANAGED": server_id}},
                {"runtime_family": {"openclaw": family_id}},
                uuid4(), "tester",
            )
        self.assertTrue(any(
            "UPDATE asset.runtime_instance" in item.args[0]
            for item in connection.execute.call_args_list
        ))

        connection.reset_mock()
        connection.execute.side_effect = execute

        def managed_execute(sql, params=None):
            cursor = execute(sql, params)
            if "count(member.deployment_member_id)" in sql:
                cursor.fetchone.return_value = {
                    "active_deployment_count": 2,
                    "current_member_count": 2,
                    "all_members_unmanaged": False,
                }
            return cursor

        connection.execute.side_effect = managed_execute
        with (
            patch.object(service, "_json", return_value=before),
            patch.object(service, "_active_operation", return_value=None),
            self.assertRaisesRegex(ConflictError, "仍有关联智能体"),
        ):
            service._commit_runtime(
                connection, row,
                {"server": {"SRV-UNMANAGED": server_id}},
                {"runtime_family": {"openclaw": family_id}},
                uuid4(), "tester",
            )
        self.assertFalse(any(
            "UPDATE asset.runtime_instance" in item.args[0]
            for item in connection.execute.call_args_list
        ))

    def test_valid_five_entity_workbook_resolves_same_batch_references(self):
        def configure(workbook):
            workbook["部门"].append(["DEP-TEST", None, "测试部门", True])
            workbook["人员"].append(["EMP-TEST", "测试人员", "DEP-TEST", "active"])
            workbook["服务器"].append(["SRV-TEST", "prod", None, "测试服务器", "planned"])
            workbook["运行时"].append(["RT-TEST", "SRV-TEST", "openclaw", "测试运行时", "gateway", "baremetal"])
            workbook["智能体"].append(["AGT-TEST", "测试智能体", "primary", None, "department", "requested", "DEP-TEST", None, None, None, "DEP-TEST"])

        rows = BulkImportService._parse("valid.xlsx", self.workbook_bytes(configure))
        BulkImportService._validate_relations(rows, self.empty_state())
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(not row["issues"] for row in rows))

    def test_formulas_and_agent_shape_errors_are_reported_during_preview(self):
        def configure(workbook):
            workbook["部门"].append(["=UPPER(\"dep-test\")", None, "测试部门", True])
            workbook["智能体"].append([
                "AGT-TEST", "测试智能体", "primary", None, "personal", "requested",
                None, None, None, "EMP-TEST", "DEP-MUST-NOT-BE-SET",
            ])

        rows = BulkImportService._parse("invalid.xlsx", self.workbook_bytes(configure))
        state = self.empty_state()
        state["person"]["EMP-TEST"] = {"id": "person-id", "row_version": 1, "payload": {}}
        state["department"]["DEP-MUST-NOT-BE-SET"] = {"id": "department-id", "row_version": 1, "payload": {}}
        BulkImportService._validate_relations(rows, state)
        issue_codes = {code for row in rows for _, code, _ in row["issues"]}
        self.assertIn("FORMULA_NOT_ALLOWED", issue_codes)
        self.assertIn("INVALID_SERVICE_FIELDS", issue_codes)

    def test_cyclic_departments_are_rejected_before_staging(self):
        def configure(workbook):
            workbook["部门"].append(["DEP-A", "DEP-B", "部门 A", True])
            workbook["部门"].append(["DEP-B", "DEP-A", "部门 B", True])

        rows = BulkImportService._parse("cycle.xlsx", self.workbook_bytes(configure))
        BulkImportService._validate_relations(rows, self.empty_state())
        self.assertTrue(all(any(code == "HIERARCHY_CYCLE" for _, code, _ in row["issues"]) for row in rows))

    def test_new_subagent_import_is_blocked_while_parent_deployment_task_is_active(self):
        parent_id = uuid4()
        object_id = uuid4()
        run_id = uuid4()
        connection = MagicMock()

        def execute(sql, params=None):
            cursor = MagicMock()
            if "SELECT to_jsonb(record)" in sql:
                cursor.fetchone.return_value = None
            elif "SELECT run.run_id" in sql:
                cursor.fetchone.return_value = {"run_id": run_id}
            else:
                cursor.fetchone.return_value = None
            return cursor

        connection.execute.side_effect = execute
        row = {
            "normalized_payload": {
                "agent_code": "AGT-NEW-CHILD",
                "name": "并发导入子智能体",
                "structure_role": "subagent",
                "parent_agent_code": "AGT-PARENT",
                "service_archetype": "business",
                "lifecycle_status": "requested",
                "owning_department_code": None,
                "business_admin_employee_no": None,
                "it_admin_employee_no": None,
                "primary_service_employee_no": None,
                "primary_service_department_code": None,
                "business_domain": None,
                "business_scenario": None,
                "description": None,
            },
            "target_object_id": object_id,
            "expected_row_version": None,
            "changed_fields": [],
        }
        ids = {
            "agent": {"AGT-PARENT": parent_id},
            "department": {},
            "person": {},
        }
        with self.assertRaisesRegex(ConflictError, str(run_id)):
            BulkImportService(database=MagicMock())._commit_agent(
                connection, row, ids, uuid4(), "tester",
            )
        self.assertTrue(any(
            "pg_advisory_xact_lock" in call.args[0]
            and call.args[1]["lock_key"] == f"agent-operation:{parent_id}"
            for call in connection.execute.call_args_list
        ))
        self.assertFalse(any(
            "INSERT INTO asset.agent_asset" in call.args[0]
            for call in connection.execute.call_args_list
        ))

    def test_moving_subagent_import_locks_and_checks_both_old_and_new_parents(self):
        old_parent_id = uuid4()
        new_parent_id = uuid4()
        object_id = uuid4()
        run_id = uuid4()
        active_parent_id = max((old_parent_id, new_parent_id), key=str)
        connection = MagicMock()

        def execute(sql, params=None):
            cursor = MagicMock()
            if "SELECT to_jsonb(record)" in sql:
                cursor.fetchone.return_value = {
                    "value": {
                        "agent_id": object_id,
                        "structure_role": "subagent",
                        "parent_agent_id": old_parent_id,
                    },
                }
            elif "SELECT run.run_id" in sql:
                cursor.fetchone.return_value = (
                    {"run_id": run_id}
                    if params["object_id"] == active_parent_id else None
                )
            else:
                cursor.fetchone.return_value = None
            return cursor

        connection.execute.side_effect = execute
        row = {
            "normalized_payload": {
                "agent_code": "AGT-MOVED-CHILD",
                "name": "迁移父级子智能体",
                "structure_role": "subagent",
                "parent_agent_code": "AGT-NEW-PARENT",
                "service_archetype": "business",
                "lifecycle_status": "requested",
                "owning_department_code": None,
                "business_admin_employee_no": None,
                "it_admin_employee_no": None,
                "primary_service_employee_no": None,
                "primary_service_department_code": None,
                "business_domain": None,
                "business_scenario": None,
                "description": None,
            },
            "target_object_id": object_id,
            "expected_row_version": 1,
            "changed_fields": ["parent_agent_code"],
        }
        ids = {
            "agent": {"AGT-NEW-PARENT": new_parent_id},
            "department": {},
            "person": {},
        }
        with self.assertRaisesRegex(ConflictError, str(run_id)):
            BulkImportService(database=MagicMock())._commit_agent(
                connection, row, ids, uuid4(), "tester",
            )
        lock_keys = {
            call.args[1]["lock_key"]
            for call in connection.execute.call_args_list
            if "pg_advisory_xact_lock" in call.args[0]
        }
        self.assertEqual(lock_keys, {
            f"agent-operation:{old_parent_id}",
            f"agent-operation:{new_parent_id}",
        })
        self.assertFalse(any(
            "UPDATE asset.agent_asset" in call.args[0]
            for call in connection.execute.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
