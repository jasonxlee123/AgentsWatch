import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "postgres"


class PostgresDeploymentTest(unittest.TestCase):
    def read(self, name: str) -> str:
        return (DEPLOY / name).read_text(encoding="utf-8")

    def test_expected_files_exist(self):
        self.assertEqual(
            {
                ".env.production.example",
                "README.md",
                "compose.application-network.yml",
                "compose.production.yml",
                "pg_hba.conf",
                "prepare_tls.sh",
            },
            {item.name for item in DEPLOY.iterdir() if item.is_file()},
        )

    def test_database_is_internal_tls_only_and_persistent(self):
        compose = self.read("compose.production.yml")
        service = compose.split("services:", 1)[1].split("\nnetworks:", 1)[0]
        self.assertNotRegex(service, r"(?m)^\s+ports:\s*$")
        self.assertIn("pull_policy: never", service)
        self.assertIn("ssl=on", service)
        self.assertIn("ssl_min_protocol_version=TLSv1.2", service)
        self.assertIn("password_encryption=scram-sha-256", service)
        self.assertIn("POSTGRES_PASSWORD_FILE", service)
        self.assertNotRegex(service, r"(?m)^\s+POSTGRES_PASSWORD:")
        self.assertIn("/data/agentswatch/management/postgres", service)
        self.assertIn("aliases:\n          - agentswatch-db", service)
        network = compose.split("\nnetworks:", 1)[1]
        self.assertIn("internal: true", network)

    def test_application_overlay_limits_database_clients(self):
        overlay = self.read("compose.application-network.yml")
        self.assertIn("management-api:", overlay)
        self.assertIn("observability-control:", overlay)
        self.assertNotIn("management-mcp:", overlay)
        self.assertNotIn("combined-nginx:", overlay)
        self.assertIn("external: true", overlay)

    def test_hba_rejects_plaintext_and_requires_scram(self):
        hba = self.read("pg_hba.conf")
        self.assertRegex(hba, r"(?m)^hostnossl\s+all\s+all\s+0\.0\.0\.0/0\s+reject$")
        self.assertRegex(hba, r"(?m)^hostssl\s+agentswatch\s+all\s+0\.0\.0\.0/0\s+scram-sha-256$")
        self.assertNotRegex(hba, r"(?m)^host\s+all\s+all\s+0\.0\.0\.0/0\s+(?:trust|md5|scram-sha-256)$")

    def test_examples_and_tls_script_do_not_embed_secrets(self):
        env = self.read(".env.production.example")
        script = self.read("prepare_tls.sh")
        self.assertRegex(env, r"(?m)^AGENTSWATCH_POSTGRES_IMAGE=\s*$")
        self.assertRegex(env, r"(?m)^AGENTSWATCH_POSTGRES_ADMIN_USER=\s*$")
        self.assertNotRegex(env, r"(?i)(password|secret|token)=[^\s]+")
        self.assertIn("subjectAltName=DNS:${database_dns_name}", script)
        self.assertIn("refusing to overwrite", script)
        self.assertIn("stat -c '%u %g'", script)
        self.assertIn("public_key_fingerprint", script)
        self.assertIn('0:${postgres_gid}:640', script)

    def run_tls_prepare_with_identity(
        self, owner_uid: int, owner_gid: int, explicit_gid: int | None = None
    ) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            data_root = root / "pgdata"
            data_root.mkdir()
            install_log = root / "install.log"

            commands = {
                "id": """#!/bin/sh
if [ "${1:-}" = "-u" ]; then
  printf '0\\n'
  exit 0
fi
exec /usr/bin/id "$@"
""",
                "stat": """#!/bin/sh
if [ "${1:-}" = "-c" ] && [ "${2:-}" = "%u %g" ] && [ "${3:-}" = "$FAKE_PGDATA" ]; then
  printf '%s %s\\n' "$FAKE_PGDATA_UID" "$FAKE_PGDATA_GID"
  exit 0
fi
exit 64
""",
                "openssl": """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-out" ] && [ "$#" -gt 1 ]; then
    shift
    : > "$1"
  fi
  shift
done
""",
                "install": """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_INSTALL_LOG"
""",
            }
            for name, content in commands.items():
                executable = fake_bin / name
                executable.write_text(content, encoding="utf-8")
                executable.chmod(0o755)

            env = os.environ.copy()
            env.pop("AGENTSWATCH_POSTGRES_GID", None)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "FAKE_INSTALL_LOG": str(install_log),
                    "FAKE_PGDATA": str(data_root),
                    "FAKE_PGDATA_UID": str(owner_uid),
                    "FAKE_PGDATA_GID": str(owner_gid),
                    "AGENTSWATCH_POSTGRES_DATA_ROOT": str(data_root),
                    "AGENTSWATCH_POSTGRES_CONFIG_ROOT": str(root / "postgres-config"),
                    "AGENTSWATCH_POSTGRES_TLS_DIR": str(root / "postgres-config" / "tls"),
                    "AGENTSWATCH_POSTGRES_PRIVATE_DIR": str(
                        root / "postgres-config" / "private"
                    ),
                    "AGENTSWATCH_DATABASE_CA_DIR": str(root / "database-ca"),
                }
            )
            if explicit_gid is not None:
                env["AGENTSWATCH_POSTGRES_GID"] = str(explicit_gid)

            subprocess.run(
                ["bash", str(DEPLOY / "prepare_tls.sh")],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            return install_log.read_text(encoding="utf-8")

    def test_tls_gid_falls_back_to_non_root_pgdata_owner_when_group_is_root(self):
        log = self.run_tls_prepare_with_identity(owner_uid=70, owner_gid=0)
        self.assertRegex(log, r"(?m)^-o root -g 70 -m 0640 .*/server\.key .*/server\.key$")

    def test_tls_gid_keeps_non_root_pgdata_group(self):
        log = self.run_tls_prepare_with_identity(owner_uid=70, owner_gid=999)
        self.assertRegex(log, r"(?m)^-o root -g 999 -m 0640 .*/server\.key .*/server\.key$")

    def test_explicit_tls_gid_overrides_pgdata_identity(self):
        log = self.run_tls_prepare_with_identity(owner_uid=70, owner_gid=0, explicit_gid=1234)
        self.assertRegex(log, r"(?m)^-o root -g 1234 -m 0640 .*/server\.key .*/server\.key$")


if __name__ == "__main__":
    unittest.main()
