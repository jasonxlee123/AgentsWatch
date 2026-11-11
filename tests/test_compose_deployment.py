import re
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ComposeDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (ROOT / "docker-compose.yml").read_text()
        cls.env_example = (ROOT / ".env.example").read_text()

    def test_all_state_uses_the_data_disk_without_named_volumes(self):
        self.assertNotRegex(self.compose, r"(?m)^volumes:\s*$")
        self.assertIn("AGENTSWATCH_DATA_ROOT=/data/agentswatch", self.env_example)
        self.assertIn("AGENTSWATCH_OBSERVABILITY_CONTROL_UID=10001", self.env_example)
        self.assertIn("AGENTSWATCH_OBSERVABILITY_CONTROL_GID=10001", self.env_example)

        for directory in (
            "minio",
            "clickhouse",
            "prometheus",
            "alertmanager",
            "otel-collector",
            "loki",
            "tempo",
            "grafana",
            "thanos-store",
            "thanos-compact",
            "observability-control",
        ):
            mount = f"${{AGENTSWATCH_DATA_ROOT:-/data/agentswatch}}/{directory}"
            self.assertIn(mount, self.compose)

    def test_startup_contains_all_permission_initializers(self):
        for service in (
            "storage-init",
            "prometheus-secrets-init",
            "nginx-tls-init",
        ):
            self.assertRegex(self.compose, rf"(?m)^  {re.escape(service)}:$")

        self.assertNotIn("otel-collector-storage-init", self.compose)
        self.assertGreaterEqual(
            self.compose.count("condition: service_completed_successfully"),
            12,
        )

    def test_observability_proxy_does_not_embed_management_module(self):
        nginx = (ROOT / "nginx" / "nginx.conf").read_text()
        self.assertNotIn("location ^~ /agentswatch/", nginx)
        self.assertNotIn("location ^~ /openclaw/", nginx)
        self.assertNotIn("/data/agentswatch/management", nginx)
        self.assertNotIn("/data/agentswatch/management", self.compose)
        self.assertNotIn("/etc/nginx/auth/agentswatch.htpasswd", self.compose)

    def test_prometheus_reads_only_the_active_ops_inventory_revision(self):
        prometheus = (ROOT / "prometheus" / "prometheus.yml").read_text()
        self.assertIn(
            "/etc/prometheus/agentswatch-control/current/*.rules.yml",
            prometheus,
        )
        self.assertIn(
            "/etc/prometheus/agentswatch-control/current/openclaw-gateways.yml",
            prometheus,
        )
        self.assertNotIn("/etc/prometheus/scrape_configs", prometheus)
        self.assertIn(
            "observability-control:/etc/prometheus/agentswatch-control:ro",
            self.compose,
        )
        self.assertNotIn(
            "./prometheus/scrape_configs:/etc/prometheus/scrape_configs",
            self.compose,
        )
        self.assertRegex(
            self.compose,
            r"group_add:\s*\n\s*- \$\{AGENTSWATCH_OBSERVABILITY_CONTROL_GID:-10001\}",
        )
        self.assertIn("bootstrap-observability-control-state", self.compose)
        self.assertNotIn("chown 10001:10001 /mnt/observability-control", self.compose)
        self.assertNotIn("management-stack", self.compose)

    def test_fresh_observability_state_bootstraps_empty_prometheus_revision(self):
        script = ROOT / "scripts" / "bootstrap_observability_control_state.sh"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "observability-control"
            state.mkdir()
            writer = (os.getuid(), os.getgid())

            subprocess.run(
                ["sh", script, state, str(writer[0]), str(writer[1])],
                check=True,
            )

            self.assertEqual((state.stat().st_uid, state.stat().st_gid), writer)
            self.assertIn(state.stat().st_mode & 0o7777, {0o2750, 0o750})
            self.assertEqual(
                ((state / "revisions").stat().st_uid, (state / "revisions").stat().st_gid),
                writer,
            )
            self.assertEqual(
                ((state / "staging").stat().st_uid, (state / "staging").stat().st_gid),
                writer,
            )
            self.assertIn(
                (state / "staging").stat().st_mode & 0o7777,
                {0o2750, 0o750},
            )
            self.assertTrue((state / "current").is_symlink())
            self.assertEqual(os.readlink(state / "current"), "revisions/bootstrap")
            revision = (state / "current").resolve()
            scrape = yaml.safe_load((revision / "openclaw-gateways.yml").read_text())
            rules = yaml.safe_load(
                (revision / "agentswatch-gateway-info.rules.yml").read_text()
            )
            manifest = json.loads((revision / "manifest.json").read_text())
            self.assertEqual((revision.stat().st_uid, revision.stat().st_gid), writer)
            self.assertIn(revision.stat().st_mode & 0o7777, {0o2750, 0o750})
            for artifact in revision.glob("*.*"):
                self.assertEqual(artifact.stat().st_mode & 0o777, 0o640)
            self.assertEqual(scrape, {"scrape_configs": []})
            self.assertEqual(rules["groups"][0]["rules"], [])
            self.assertEqual(manifest["revision_id"], "bootstrap")

            subprocess.run(
                ["sh", script, state, str(writer[0]), str(writer[1])],
                check=True,
            )
            self.assertEqual(os.readlink(state / "current"), "revisions/bootstrap")


if __name__ == "__main__":
    unittest.main()
