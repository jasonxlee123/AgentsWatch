import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_observability_gateway_inventory.py"
SPEC = importlib.util.spec_from_file_location("sync_observability_gateway_inventory", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def gateway_row(**overrides):
    row = {
        "runtime_id": "00000000-0000-4000-8000-000000000701",
        "gateway_id": "gateway-701",
        "server_id": "00000000-0000-4000-8000-000000000501",
        "server_display_id": "ECS-001",
        "monitor_host": "10.0.0.11",
        "monitor_port": 16402,
        "monitoring_enabled": True,
        "monitoring_ready": True,
        "environment_code": "prod",
        "region": "cn-beijing",
        "operations_team": "platform-ops",
        "deployment_mode": "baremetal",
        "lobster_name": "员工个人数字助手",
        "department_name": "平台运维部",
        "display_name": "员工个人数字助手-ECS-001",
    }
    row.update(overrides)
    return row


class ObservabilityGatewayInventoryTest(unittest.TestCase):
    def test_builds_generator_inventory_with_runtime_join_keys_and_display_name(self):
        inventory = MODULE.build_inventory(
            [gateway_row()], "http://otel-collector:4318", cluster="lobster-prod",
        )

        self.assertEqual(inventory["defaults"]["otel_endpoint"], "http://otel-collector:4318")
        gateway = inventory["gateways"][0]
        self.assertEqual(gateway["runtime_id"], "00000000-0000-4000-8000-000000000701")
        self.assertEqual(gateway["server_display_id"], "ECS-001")
        self.assertEqual(gateway["display_name"], "员工个人数字助手-ECS-001")
        self.assertEqual(gateway["cluster"], "lobster-prod")

    def test_excludes_incomplete_rows_and_rejects_duplicate_gateway_identity(self):
        inventory = MODULE.build_inventory(
            [gateway_row(monitoring_ready=False)], "http://otel-collector:4318",
        )
        self.assertEqual(inventory["gateways"], [])

        with self.assertRaisesRegex(ValueError, "duplicate gateway_id"):
            MODULE.build_inventory(
                [gateway_row(), gateway_row(runtime_id="00000000-0000-4000-8000-000000000702")],
                "http://otel-collector:4318",
            )


if __name__ == "__main__":
    unittest.main()
