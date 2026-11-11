import os
import unittest
from pathlib import Path
from unittest.mock import patch

from agentswatch_api.runtime_management import (
    unmanaged_runtime_metadata_update_allowed,
    runtime_operation_become,
    validate_runtime_connection_policy,
)


ROOT = Path(__file__).resolve().parents[1]


class RuntimePrivilegeContractTests(unittest.TestCase):
    def test_unmanaged_import_can_only_complete_missing_runtime_control_metadata(self):
        active_unmanaged = {
            "active_deployment_count": 2,
            "current_member_count": 2,
            "all_members_unmanaged": True,
        }
        before = {
            "server_id": "server-a",
            "runtime_family_id": "openclaw",
            "deployment_mode": "baremetal",
            "lifecycle_status": "active",
            "config_path": None,
            "state_path": None,
            "management_driver": "none",
            "management_target": None,
        }
        self.assertTrue(unmanaged_runtime_metadata_update_allowed(
            active_unmanaged,
            before,
            {
                "config_path": "/home/openclaw/.openclaw/openclaw.json",
                "state_path": "/home/openclaw/.openclaw",
                "management_driver": "systemd_user",
                "management_target": "openclaw-gateway.service",
            },
        ))
        self.assertTrue(unmanaged_runtime_metadata_update_allowed(
            active_unmanaged,
            {**before, "config_path": "/wrong/nonempty/path"},
            {"config_path": "/correct/path"},
        ))
        self.assertFalse(unmanaged_runtime_metadata_update_allowed(
            active_unmanaged,
            before,
            {"server_id": "server-b"},
        ))

    def test_verified_or_mixed_deployment_cannot_use_unmanaged_metadata_exception(self):
        before = {
            "config_path": None,
            "state_path": None,
            "management_driver": "none",
            "management_target": None,
        }
        for deployment in (
            {
                "active_deployment_count": 2,
                "current_member_count": 2,
                "all_members_unmanaged": False,
            },
            {
                "active_deployment_count": 1,
                "current_member_count": 0,
                "all_members_unmanaged": False,
            },
        ):
            with self.subTest(deployment=deployment):
                self.assertFalse(unmanaged_runtime_metadata_update_allowed(
                    deployment, before, {"config_path": "/managed/config"},
                ))

    def test_disabled_local_connection_rejects_collection_and_lifecycle_operations(self):
        with patch.dict(os.environ, {"AGENTSWATCH_ALLOW_LOCAL_CONNECTION": "false"}):
            for operation in ("runtime.collect_info", "runtime.start"):
                with self.subTest(operation=operation), self.assertRaisesRegex(
                    ValueError, "本机连接未启用",
                ):
                    validate_runtime_connection_policy(operation, "local", "systemd_user")

    def test_enabled_local_connection_preserves_mock_development_operations(self):
        with patch.dict(os.environ, {"AGENTSWATCH_ALLOW_LOCAL_CONNECTION": "true"}):
            validate_runtime_connection_policy("runtime.collect_info", "local", "mock")
            validate_runtime_connection_policy("runtime.start", "local", "mock")

    def test_system_service_mutations_require_configured_become(self):
        for operation in ("runtime.start", "runtime.stop", "runtime.restart"):
            with self.assertRaisesRegex(ValueError, "sudo"):
                runtime_operation_become(operation, "systemd", False)
            self.assertTrue(runtime_operation_become(operation, "systemd", True))

    def test_read_only_and_user_service_operations_never_become(self):
        self.assertFalse(runtime_operation_become("runtime.collect_info", "systemd", True))
        self.assertFalse(runtime_operation_become("runtime.refresh_status", "systemd", True))
        for operation in (
            "runtime.collect_info", "runtime.refresh_status", "runtime.start",
            "runtime.stop", "runtime.restart",
        ):
            self.assertFalse(runtime_operation_become(operation, "systemd_user", True))

    def test_systemd_user_role_has_explicit_user_bus_and_strict_status_codes(self):
        role = (ROOT / "automation/roles/runtime_control/tasks/main.yml").read_text(encoding="utf-8")
        self.assertIn("scope: user", role)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", role)
        self.assertIn("XDG_RUNTIME_DIR", role)
        self.assertIn("systemctl\n      - --user", role)
        self.assertNotIn("runtime_systemd_status.rc not in [0, 3, 4]", role)

    def test_runtime_collection_prefers_configured_absolute_command(self):
        playbook = (ROOT / "automation/playbooks/runtime/collect_info.yml").read_text(encoding="utf-8")
        self.assertIn("Inspect configured runtime command", playbook)
        self.assertIn("follow: true", playbook)
        self.assertIn("runtime_resolved_command_path", playbook)
        self.assertNotIn("/home/openclaw", playbook)


if __name__ == "__main__":
    unittest.main()
