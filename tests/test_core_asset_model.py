import copy
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "core_assets_valid.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = load_module(
    "validate_core_asset_model",
    ROOT / "scripts" / "validate_core_asset_model.py",
)


class CoreAssetModelValidationTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text())

    def validate(self, payload=None):
        return validator.validate_payload(payload or self.payload)

    def issue_codes(self, payload=None):
        return {item["code"] for item in self.validate(payload)["issues"]}

    def test_valid_fixture_exercises_runtime_one_to_one_and_multi_group_membership(self):
        result = self.validate()

        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["summary"]["shared_runtime_count"], 0)
        self.assertEqual(result["summary"]["max_primary_agents_per_runtime"], 1)
        self.assertEqual(result["summary"]["servers_in_multiple_groups"], 1)
        self.assertEqual(result["summary"]["agents_in_multiple_groups"], 1)

    def test_second_active_primary_agent_for_same_runtime_is_rejected(self):
        deployment = self.payload["agent_deployments"][1]
        deployment["runtime_id"] = self.payload["agent_deployments"][0]["runtime_id"]
        for member in self.payload["agent_deployment_members"]:
            if member["deployment_id"] == deployment["deployment_id"]:
                member["runtime_id"] = deployment["runtime_id"]

        self.assertIn("ACTIVE_RUNTIME_DEPLOYMENT_CONFLICT", self.issue_codes())

    def test_second_active_deployment_for_same_primary_agent_is_rejected(self):
        deployment = copy.deepcopy(self.payload["agent_deployments"][0])
        deployment["deployment_id"] = "00000000-0000-4000-8000-000000009801"
        self.payload["agent_deployments"].append(deployment)

        member = copy.deepcopy(self.payload["agent_deployment_members"][0])
        member["deployment_member_id"] = "00000000-0000-4000-8000-000000009901"
        member["deployment_id"] = deployment["deployment_id"]
        member["runtime_agent_key"] = "assistant-duplicate-deployment"
        self.payload["agent_deployment_members"].append(member)

        self.assertIn("ACTIVE_AGENT_DEPLOYMENT_CONFLICT", self.issue_codes())

    def test_subagent_cannot_join_management_group_directly(self):
        self.payload["agent_group_members"].append(
            {
                "member_id": "00000000-0000-4000-8000-000000009101",
                "group_id": "00000000-0000-4000-8000-000000000503",
                "agent_id": "00000000-0000-4000-8000-000000000711",
                "active": True,
            }
        )

        self.assertIn("SUBAGENT_GROUP_MEMBERSHIP", self.issue_codes())

    def test_subagent_must_inherit_parent_service_archetype(self):
        subagent = next(item for item in self.payload["agents"] if item["structure_role"] == "subagent")
        subagent["service_archetype"] = "business"

        self.assertIn("SUBAGENT_ARCHETYPE_MISMATCH", self.issue_codes())

    def test_container_runtime_requires_container_identity_and_image(self):
        runtime = next(item for item in self.payload["runtimes"] if item["deployment_mode"] == "container")
        runtime["image_ref"] = None

        self.assertIn("RUNTIME_MODE_FIELDS_INVALID", self.issue_codes())

    def test_runtime_agent_key_is_unique_within_active_runtime(self):
        first, second = self.payload["agent_deployment_members"][0:2]
        second["runtime_agent_key"] = first["runtime_agent_key"]

        self.assertIn("RUNTIME_AGENT_KEY_DUPLICATE", self.issue_codes())

    def test_active_primary_agent_requires_one_business_and_it_admin(self):
        primary = next(item for item in self.payload["agents"] if item["structure_role"] == "primary")
        primary["business_admin_person_id"] = None

        self.assertIn("ACTIVE_AGENT_ADMIN_REQUIRED", self.issue_codes())

    def test_server_capacity_must_be_positive(self):
        self.payload["servers"][0]["memory_gib"] = 0

        self.assertIn("SERVER_CAPACITY_INVALID", self.issue_codes())

    def test_lifecycle_must_use_defined_enum(self):
        self.payload["agents"][0]["lifecycle_status"] = "online"

        self.assertIn("ENUM_VALUE_INVALID", self.issue_codes())

    def test_versioned_object_requires_positive_row_version(self):
        self.payload["servers"][0]["row_version"] = 0

        self.assertIn("ROW_VERSION_INVALID", self.issue_codes())

    def test_active_deployment_cannot_point_to_retired_runtime(self):
        self.payload["runtimes"][0]["lifecycle_status"] = "retired"

        self.assertIn("ACTIVE_DEPLOYMENT_RUNTIME_STATE_INVALID", self.issue_codes())

    def test_primary_agent_migration_preserves_agent_identity(self):
        agent_id = "00000000-0000-4000-8000-000000000701"
        old_deployment = next(
            item for item in self.payload["agent_deployments"] if item["primary_agent_id"] == agent_id
        )
        old_deployment["lifecycle_status"] = "inactive"
        old_deployment["row_version"] += 1

        new_runtime = copy.deepcopy(self.payload["runtimes"][0])
        new_runtime.update(
            {
                "runtime_id": "00000000-0000-4000-8000-000000006901",
                "server_id": "00000000-0000-4000-8000-000000000402",
                "name": "OpenClaw 迁移目标 Runtime",
                "deployment_mode": "container",
                "container_name": "openclaw-migrated",
                "image_ref": "registry.example/openclaw:2026.6",
                "row_version": 1,
            }
        )
        self.payload["runtimes"].append(new_runtime)

        new_deployment = {
            "deployment_id": "00000000-0000-4000-8000-000000008901",
            "primary_agent_id": agent_id,
            "runtime_id": new_runtime["runtime_id"],
            "lifecycle_status": "active",
            "row_version": 1,
        }
        self.payload["agent_deployments"].append(new_deployment)

        old_members = [
            item
            for item in self.payload["agent_deployment_members"]
            if item["deployment_id"] == old_deployment["deployment_id"]
        ]
        new_primary_member_id = "00000000-0000-4000-8000-000000009801"
        for position, old_member in enumerate(old_members):
            member = copy.deepcopy(old_member)
            member["deployment_member_id"] = (
                new_primary_member_id
                if member["member_role"] == "primary"
                else "00000000-0000-4000-8000-000000009811"
            )
            member["deployment_id"] = new_deployment["deployment_id"]
            member["runtime_id"] = new_runtime["runtime_id"]
            member["parent_member_id"] = None if member["member_role"] == "primary" else new_primary_member_id
            self.payload["agent_deployment_members"].append(member)

        result = self.validate()
        active = [
            item
            for item in self.payload["agent_deployments"]
            if item["primary_agent_id"] == agent_id and item["lifecycle_status"] == "active"
        ]
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["runtime_id"], new_runtime["runtime_id"])
        self.assertEqual(active[0]["primary_agent_id"], agent_id)

    def test_runtime_rebuild_moves_its_active_binding_atomically(self):
        old_runtime = self.payload["runtimes"][0]
        old_runtime_id = old_runtime["runtime_id"]
        old_runtime["lifecycle_status"] = "retired"
        old_runtime["row_version"] += 1

        new_runtime = copy.deepcopy(old_runtime)
        new_runtime.update(
            {
                "runtime_id": "00000000-0000-4000-8000-000000006902",
                "name": "OpenClaw Gateway 重建实例",
                "lifecycle_status": "active",
                "row_version": 1,
            }
        )
        self.payload["runtimes"].append(new_runtime)

        old_deployments = [
            item
            for item in list(self.payload["agent_deployments"])
            if item["runtime_id"] == old_runtime_id and item["lifecycle_status"] == "active"
        ]
        for old_deployment in old_deployments:
            old_deployment["lifecycle_status"] = "inactive"
            old_deployment["row_version"] += 1
            new_deployment = copy.deepcopy(old_deployment)
            new_deployment["deployment_id"] = str(
                uuid.uuid5(uuid.NAMESPACE_DNS, f"rebuilt-deployment:{old_deployment['deployment_id']}")
            )
            new_deployment["runtime_id"] = new_runtime["runtime_id"]
            new_deployment["lifecycle_status"] = "active"
            new_deployment["row_version"] = 1
            self.payload["agent_deployments"].append(new_deployment)

            old_members = [
                item
                for item in list(self.payload["agent_deployment_members"])
                if item["deployment_id"] == old_deployment["deployment_id"]
            ]
            member_ids = {
                member["deployment_member_id"]: str(
                    uuid.uuid5(uuid.NAMESPACE_DNS, f"rebuilt-member:{member['deployment_member_id']}")
                )
                for member in old_members
            }
            for old_member in old_members:
                new_member = copy.deepcopy(old_member)
                new_member["deployment_member_id"] = member_ids[old_member["deployment_member_id"]]
                new_member["deployment_id"] = new_deployment["deployment_id"]
                new_member["runtime_id"] = new_runtime["runtime_id"]
                if old_member["parent_member_id"]:
                    new_member["parent_member_id"] = member_ids[old_member["parent_member_id"]]
                self.payload["agent_deployment_members"].append(new_member)

        result = self.validate()
        rebuilt_active = [
            item
            for item in self.payload["agent_deployments"]
            if item["runtime_id"] == new_runtime["runtime_id"] and item["lifecycle_status"] == "active"
        ]
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(len(rebuilt_active), 1)
        self.assertEqual(
            {item["primary_agent_id"] for item in rebuilt_active},
            {item["primary_agent_id"] for item in old_deployments},
        )

    def test_retiring_agent_ends_deployment_without_changing_identity(self):
        agent_id = "00000000-0000-4000-8000-000000000704"
        agent = next(item for item in self.payload["agents"] if item["agent_id"] == agent_id)
        deployment = next(
            item for item in self.payload["agent_deployments"] if item["primary_agent_id"] == agent_id
        )
        agent["lifecycle_status"] = "retired"
        agent["row_version"] += 1
        deployment["lifecycle_status"] = "retired"
        deployment["row_version"] += 1

        result = self.validate()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(agent["agent_id"], agent_id)
        self.assertFalse(
            any(
                item["primary_agent_id"] == agent_id and item["lifecycle_status"] == "active"
                for item in self.payload["agent_deployments"]
            )
        )

    def test_deployment_member_runtime_must_match_deployment(self):
        self.payload["agent_deployment_members"][0]["runtime_id"] = (
            "00000000-0000-4000-8000-000000000602"
        )

        self.assertIn("DEPLOYMENT_MEMBER_RUNTIME_MISMATCH", self.issue_codes())

    def test_deployment_requires_exactly_one_primary_member(self):
        deployment_id = self.payload["agent_deployments"][0]["deployment_id"]
        self.payload["agent_deployment_members"] = [
            item
            for item in self.payload["agent_deployment_members"]
            if not (
                item["deployment_id"] == deployment_id and item["member_role"] == "primary"
            )
        ]

        self.assertIn("DEPLOYMENT_PRIMARY_MEMBER_INVALID", self.issue_codes())

    def test_active_runtime_requires_active_or_maintenance_server(self):
        self.payload["runtimes"][0]["server_id"] = "00000000-0000-4000-8000-000000000403"

        self.assertIn("ACTIVE_RUNTIME_SERVER_STATE_INVALID", self.issue_codes())


if __name__ == "__main__":
    unittest.main()
