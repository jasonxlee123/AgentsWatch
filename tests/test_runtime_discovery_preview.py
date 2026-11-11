import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "runtime_discovery"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preview = load_module("preview_runtime_discovery", ROOT / "scripts" / "preview_runtime_discovery.py")


class RuntimeDiscoveryPreviewTests(unittest.TestCase):
    def load(self, name):
        return json.loads((FIXTURES / name).read_text())

    def test_openclaw_shared_runtime_is_normalized_and_redacted(self):
        report = preview.preview_discovery(self.load("openclaw_shared_runtime.json"))
        raw = json.dumps(report["snapshot"]["raw_payload"], ensure_ascii=False)

        self.assertTrue(report["should_create_snapshot"])
        self.assertEqual(report["summary"]["primary_agents"], 2)
        self.assertEqual(report["summary"]["subagents"], 1)
        self.assertEqual(report["snapshot"]["observed_agent_count"], 3)
        self.assertNotIn("local-fixture-token", raw)
        self.assertNotIn("sk-local-fixture", raw)
        self.assertGreaterEqual(raw.count("[REDACTED]"), 2)

    def test_hermes_uses_same_framework_neutral_projection(self):
        report = preview.preview_discovery(self.load("hermes_container_runtime.json"))

        self.assertEqual(report["runtime_family_code"], "hermes")
        self.assertEqual(report["summary"], {"primary_agents": 1, "subagents": 1, "unresolved_mappings": 2})
        self.assertEqual(
            set(report["agent_projection"][0]),
            {
                "runtime_agent_key", "name", "member_role", "parent_runtime_agent_key",
                "model", "skills", "tools", "mapping_status",
            },
        )

    def test_same_content_at_another_time_is_deduplicated(self):
        payload = self.load("openclaw_shared_runtime.json")
        first = preview.preview_discovery(payload)
        payload["discovered_at"] = "2026-07-12T09:00:00+08:00"
        second = preview.preview_discovery(payload, first["content_hash"])

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertFalse(second["should_create_snapshot"])
        self.assertIsNone(second["snapshot"])

    def test_content_change_creates_new_hash_and_snapshot(self):
        payload = self.load("openclaw_shared_runtime.json")
        first = preview.preview_discovery(payload)
        payload["actual_version"] = "2026.6.12"
        second = preview.preview_discovery(payload, first["content_hash"])

        self.assertNotEqual(first["content_hash"], second["content_hash"])
        self.assertTrue(second["should_create_snapshot"])

    def test_duplicate_runtime_agent_key_is_rejected(self):
        payload = self.load("openclaw_shared_runtime.json")
        duplicate = copy.deepcopy(payload["agents"][0])
        payload["agents"].append(duplicate)

        with self.assertRaisesRegex(ValueError, "duplicate runtime agent key"):
            preview.preview_discovery(payload)

    def test_subagent_parent_must_exist_and_be_primary(self):
        payload = self.load("openclaw_shared_runtime.json")
        payload["agents"][1]["parent_key"] = "missing-main"

        with self.assertRaisesRegex(ValueError, "does not exist"):
            preview.preview_discovery(payload)


if __name__ == "__main__":
    unittest.main()
