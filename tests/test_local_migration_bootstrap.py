import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap_local_migration_ledger.py"
SPEC = importlib.util.spec_from_file_location("local_migration_bootstrap", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class MarkerConnection:
    def __init__(self, present_versions):
        self.present_versions = set(present_versions)

    def execute(self, statement, _params=None):
        version = next(version for version, marker in MODULE.MARKERS.items() if marker == statement)
        return Result(version in self.present_versions)


class LocalMigrationBootstrapTests(unittest.TestCase):
    def test_every_formal_migration_has_an_adoption_marker(self):
        versions = {item.version for item in MODULE.discover_migrations()}
        self.assertEqual(set(MODULE.MARKERS), versions)

    def test_detects_a_verified_legacy_prefix(self):
        migrations = MODULE.discover_migrations()
        expected = migrations[:-1]
        actual = MODULE.detect_applied_prefix(
            MarkerConnection(item.version for item in expected), migrations,
        )
        self.assertEqual([item.version for item in actual], [item.version for item in expected])

    def test_rejects_a_marker_after_a_missing_migration(self):
        migrations = MODULE.discover_migrations()
        present = [item.version for item in migrations if item.version != "010"]
        with self.assertRaisesRegex(RuntimeError, "not an exact migration prefix"):
            MODULE.detect_applied_prefix(MarkerConnection(present), migrations)


if __name__ == "__main__":
    unittest.main()
