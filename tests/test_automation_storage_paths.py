import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agentswatch_api.automation_library import AutomationLibraryService
from agentswatch_automation.paths import (
    AUTOMATION_DATA_ROOT_ENV,
    CODE_AUTOMATION_ROOT,
    artifact_root,
    automation_data_root,
    managed_root,
    resolve_managed_playbook,
    runner_root,
)


class AutomationStoragePathTests(unittest.TestCase):
    def test_unconfigured_paths_preserve_local_checkout_layout(self):
        with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: ""}):
            self.assertEqual(automation_data_root(), CODE_AUTOMATION_ROOT)
            self.assertEqual(managed_root(), CODE_AUTOMATION_ROOT / "managed")
            self.assertEqual(artifact_root(), CODE_AUTOMATION_ROOT / "artifacts")
            self.assertEqual(runner_root(), CODE_AUTOMATION_ROOT / ".runner")

    def test_configured_data_root_moves_all_mutable_automation_paths(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-data-root-") as directory:
            root = Path(directory).resolve()
            with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: str(root)}):
                self.assertEqual(automation_data_root(), root)
                self.assertEqual(managed_root(), root / "managed")
                self.assertEqual(artifact_root(), root / "artifacts")
                self.assertEqual(runner_root(), root / ".runner")

    def test_configured_data_root_must_be_absolute(self):
        with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: "relative/automation-data"}):
            with self.assertRaisesRegex(ValueError, "must be an absolute path"):
                automation_data_root()

    def test_managed_logical_path_cannot_escape_configured_root(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-data-root-") as directory:
            with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: directory}):
                with self.assertRaisesRegex(ValueError, "outside configured root"):
                    resolve_managed_playbook("managed/../../outside.yml")
                with self.assertRaisesRegex(ValueError, "invalid managed playbook path"):
                    resolve_managed_playbook("playbooks/example.yml")
                with self.assertRaisesRegex(ValueError, "invalid managed playbook path"):
                    resolve_managed_playbook(Path(directory) / "managed" / "example.yml")

    def test_materialized_playbook_uses_configured_root_and_stable_logical_path(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-data-root-") as directory:
            root = Path(directory).resolve()
            automation_id = uuid4()
            version_id = uuid4()
            content_hash = "a" * 64
            content = "- hosts: all\n  gather_facts: false\n  tasks: []\n"
            with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: str(root)}):
                logical_path = AutomationLibraryService._materialize(
                    automation_id, version_id, content_hash, content,
                )
                self.assertEqual(
                    logical_path,
                    f"managed/{automation_id}/{version_id}-{content_hash[:12]}.yml",
                )
                materialized = resolve_managed_playbook(logical_path)
                self.assertEqual(materialized.read_text(encoding="utf-8"), content)
                self.assertEqual(materialized.stat().st_mode & 0o777, 0o600)
                self.assertFalse((CODE_AUTOMATION_ROOT / logical_path).exists())


if __name__ == "__main__":
    unittest.main()
