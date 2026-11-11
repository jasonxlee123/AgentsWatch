import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "configure_core_asset_roles.py"
SPEC = importlib.util.spec_from_file_location("core_asset_role_grants", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def environment(**overrides):
    values = {
        MODULE.ADMIN_URL_ENV: "postgresql://admin:admin-secret@example.invalid/agentswatch",
        MODULE.MIGRATION_OWNER_ENV: "agentswatch_migration_owner",
        MODULE.READER_ROLE_ENV: "agentswatch-reader",
        MODULE.READER_PASSWORD_ENV: "reader-password-value-123",
        MODULE.WRITER_ROLE_ENV: "agentswatch-writer",
        MODULE.WRITER_PASSWORD_ENV: "writer-password-value-456",
    }
    values.update(overrides)
    return values


class CoreAssetRoleGrantConfigTests(unittest.TestCase):
    def test_configuration_reads_all_sensitive_values_from_environment(self):
        config = MODULE.load_config(environment())

        self.assertEqual(config.migration_owner_role, "agentswatch_migration_owner")
        self.assertEqual(config.reader_role, "agentswatch-reader")
        self.assertEqual(config.writer_role, "agentswatch-writer")
        rendered = repr(config)
        self.assertNotIn("admin-secret", rendered)
        self.assertNotIn("reader-password", rendered)
        self.assertNotIn("writer-password", rendered)

    def test_identifier_injection_and_ambiguous_roles_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "PostgreSQL role name"):
            MODULE.load_config(environment(**{
                MODULE.READER_ROLE_ENV: 'reader"; DROP ROLE postgres; --',
            }))
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            MODULE.load_config(environment(**{
                MODULE.WRITER_ROLE_ENV: "agentswatch-reader",
            }))

    def test_passwords_must_be_distinct_nonempty_environment_values(self):
        with self.assertRaisesRegex(ValueError, "at least 16"):
            MODULE.load_config(environment(**{MODULE.READER_PASSWORD_ENV: "short"}))
        with self.assertRaisesRegex(ValueError, "must be different"):
            MODULE.load_config(environment(**{
                MODULE.READER_PASSWORD_ENV: "same-password-value",
                MODULE.WRITER_PASSWORD_ENV: "same-password-value",
            }))


if __name__ == "__main__":
    unittest.main()
