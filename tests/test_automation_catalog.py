import unittest

from agentswatch_automation import AutomationRunner, OperationCatalog


class AutomationCatalogTests(unittest.TestCase):
    def test_server_catalog_contains_only_approved_initial_operations(self):
        catalog = OperationCatalog()
        self.assertEqual(
            [item.code for item in catalog.list("server")],
            [
                "server.bootstrap_ssh",
                "server.check_connection",
                "server.collect_facts",
                "server.discover_runtimes",
            ],
        )

    def test_runtime_catalog_exposes_standard_lifecycle_operations(self):
        self.assertEqual(
            [item.code for item in OperationCatalog().list("runtime")],
            ["runtime.collect_info", "runtime.refresh_status", "runtime.restart", "runtime.start", "runtime.stop"],
        )

    def test_agent_catalog_exposes_controlled_deployment_operation(self):
        self.assertEqual(
            [item.code for item in OperationCatalog().list("agent")],
            [
                "agent.apply_deployment",
                "agent.migrate_deployment",
                "agent.remove_deployment",
            ],
        )
        migrate = OperationCatalog().get("agent.migrate_deployment")
        remove = OperationCatalog().get("agent.remove_deployment")
        self.assertEqual(migrate.risk_level, "high")
        self.assertEqual(remove.risk_level, "high")
        self.assertTrue(migrate.parameters["runtime_agent_key"]["required"])
        self.assertTrue(migrate.parameters["expected_deployment_id"]["required"])
        self.assertTrue(remove.parameters["expected_deployment_id"]["required"])

    def test_catalog_exposes_task_picker_metadata(self):
        operation = OperationCatalog().get("server.discover_runtimes")
        self.assertEqual(operation.category, "资产采集")
        self.assertEqual(operation.version, "1.0.0")
        self.assertTrue(operation.supports_batch)
        self.assertIn("运行时", operation.description)
        self.assertEqual(operation.parameters["runtime_search_paths"]["label"], "补充搜索路径")

    def test_catalog_centrally_validates_declared_parameter_contract(self):
        catalog = OperationCatalog()
        self.assertEqual(
            catalog.validate_parameters(
                "server.discover_runtimes",
                {"runtime_search_paths": ["/opt/openclaw", "/opt/hermes"]},
            ),
            {"runtime_search_paths": ["/opt/openclaw", "/opt/hermes"]},
        )
        with self.assertRaisesRegex(ValueError, "must be array"):
            catalog.validate_parameters("server.discover_runtimes", {"runtime_search_paths": "/tmp"})
        with self.assertRaisesRegex(ValueError, "exceeds max_items"):
            catalog.validate_parameters(
                "server.discover_runtimes",
                {"runtime_search_paths": [f"/tmp/{index}" for index in range(21)]},
            )

    def test_unknown_operation_is_rejected(self):
        with self.assertRaisesRegex(KeyError, "unknown or unapproved operation"):
            OperationCatalog().get("server.run_shell")

    def test_inventory_uses_platform_server_id_as_ansible_host_alias(self):
        inventory = AutomationRunner._inventory(
            [{"server_id": "server-001", "address": "10.0.0.10", "port": 2222, "user": "ops"}]
        )
        self.assertEqual(
            inventory["all"]["hosts"]["server-001"],
            {"ansible_host": "10.0.0.10", "ansible_port": 2222, "ansible_user": "ops"},
        )

    def test_runtime_inventory_includes_service_and_endpoint_readiness_contract(self):
        inventory = AutomationRunner._inventory([{
            "server_id": "runtime-001",
            "address": "127.0.0.1",
            "connection": "local",
            "runtime_management_driver": "launchd",
            "runtime_management_target": "ai.openclaw.gateway",
            "runtime_endpoint_port": 16402,
            "runtime_probe_host": "127.0.0.1",
            "runtime_command_path": "/opt/example/bin/openclaw",
        }])
        variables = inventory["all"]["hosts"]["runtime-001"]
        self.assertEqual(variables["runtime_endpoint_port"], 16402)
        self.assertEqual(variables["runtime_probe_host"], "127.0.0.1")
        self.assertEqual(variables["runtime_command_path"], "/opt/example/bin/openclaw")

    def test_agent_inventory_includes_only_declared_deployment_definitions(self):
        definitions = [{
            "agent_id": "agent-001",
            "name": "测试智能体",
            "structure_role": "primary",
            "runtime_agent_key": "test-agent",
            "workspace_path": "/tmp/test-agent",
            "model": "",
            "channel_type": "none",
            "channel_account_id": "aw-agent001",
        }]
        inventory = AutomationRunner._inventory([{
            "server_id": "agent-001",
            "address": "127.0.0.1",
            "connection": "local",
            "runtime_family_code": "openclaw",
            "runtime_config_path": "/tmp/openclaw.json",
            "runtime_state_path": "/tmp/openclaw-state",
            "agentswatch_retired_agent_definitions": [],
            "agentswatch_agent_definitions": definitions,
            "undeclared_secret": "must-not-pass",
        }])
        variables = inventory["all"]["hosts"]["agent-001"]
        self.assertEqual(variables["agentswatch_agent_definitions"], definitions)
        self.assertNotIn("undeclared_secret", variables)

    def test_agent_inventory_preserves_cleanup_ownership_mode(self):
        inventory = AutomationRunner._inventory([{
            "server_id": "agent-001",
            "address": "127.0.0.1",
            "connection": "local",
            "agentswatch_ownership_cleanup_mode": "compensation",
        }])
        variables = inventory["all"]["hosts"]["agent-001"]
        self.assertEqual(
            variables["agentswatch_ownership_cleanup_mode"], "compensation",
        )

    def test_empty_and_duplicate_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one target"):
            AutomationRunner._inventory([])
        target = {"server_id": "server-001", "address": "127.0.0.1"}
        with self.assertRaisesRegex(ValueError, "duplicate server target"):
            AutomationRunner._inventory([target, target])

    def test_unapproved_parameter_is_rejected_before_ansible_runs(self):
        with self.assertRaisesRegex(ValueError, "unsupported parameters"):
            AutomationRunner().run(
                "server.check_connection",
                [{"server_id": "server-001", "address": "127.0.0.1", "connection": "local"}],
                {"shell": "rm -rf /"},
            )


if __name__ == "__main__":
    unittest.main()
