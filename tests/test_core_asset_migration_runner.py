import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_core_asset_migrations.py"
SPEC = importlib.util.spec_from_file_location("core_asset_migrations", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CoreAssetMigrationRunnerTest(unittest.TestCase):
    def test_discovers_ordered_migrations_without_local_role_bootstrap(self):
        migrations = MODULE.discover_migrations()
        names = [item.path.name for item in migrations]
        self.assertEqual(names[0], "001_core_asset_schema.sql")
        self.assertEqual(names[-1], "021_agent_deployment_primary_role_integrity.sql")
        self.assertNotIn("003_local_reader.sql", names)
        self.assertEqual([item.version for item in migrations], sorted(item.version for item in migrations))

    def test_strips_only_outer_psql_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "001_example.sql"
            path.write_text(
                "\\set ON_ERROR_STOP on\n\nBEGIN;\n"
                "CREATE FUNCTION example() RETURNS void LANGUAGE plpgsql AS $$\n"
                "BEGIN\n  RETURN;\nEND;\n$$;\nCOMMIT;\n",
                encoding="utf-8",
            )
            sql = MODULE.migration_sql(path)
        self.assertNotIn("\\set", sql)
        self.assertNotIn("\nCOMMIT;", sql)
        self.assertIn("BEGIN\n  RETURN;", sql)

    def test_requires_outer_transaction_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "001_invalid.sql"
            path.write_text("SELECT 1;\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outer BEGIN/COMMIT"):
                MODULE.migration_sql(path)

    def test_rejects_any_malformed_sql_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "001_valid.sql").write_text("BEGIN;\nSELECT 1;\nCOMMIT;\n", encoding="utf-8")
            (root / "notes.sql").write_text("BEGIN;\nSELECT 2;\nCOMMIT;\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid migration filename: notes.sql"):
                MODULE.discover_migrations(root)

    def test_history_must_be_an_exact_ordered_prefix(self):
        migrations = MODULE.discover_migrations()
        first = migrations[0]
        third = migrations[2]
        applied = {
            first.version: (first.path.name, first.checksum),
            third.version: (third.path.name, third.checksum),
        }
        with self.assertRaisesRegex(RuntimeError, "exact ordered prefix"):
            MODULE.validate_history(migrations, applied)

    def test_cached_bytes_drive_checksum_and_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "001_example.sql"
            original = b"BEGIN;\nSELECT 1;\nCOMMIT;\n"
            path.write_bytes(original)
            migration = MODULE.discover_migrations(root)[0]
            path.write_bytes(b"BEGIN;\nSELECT 2;\nCOMMIT;\n")
            self.assertEqual(migration.checksum, __import__("hashlib").sha256(original).hexdigest())
            self.assertIn("SELECT 1", MODULE.migration_sql_contents(migration.contents, path.name))


if __name__ == "__main__":
    unittest.main()
