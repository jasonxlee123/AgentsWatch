import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "combined"


class CombinedDeploymentTest(unittest.TestCase):
    def read(self, name: str) -> str:
        return (DEPLOY / name).read_text(encoding="utf-8")

    def test_expected_combined_files_exist(self):
        expected = {
            ".env.operations.production.example",
            ".env.production.example",
            "Dockerfile.observability-control",
            "Dockerfile.observability-control.dockerignore",
            "README.md",
            "compose.legacy-openclaw-relay.yml",
            "compose.production-lockdown.yml",
            "compose.production.example.yml",
            "nginx-combined.conf",
            "nginx-legacy-openclaw.conf",
        }
        self.assertEqual(expected, {item.name for item in DEPLOY.iterdir() if item.is_file()})

    def test_overlay_disables_standalone_ingresses_and_publishes_one_ingress(self):
        compose = self.read("compose.production.example.yml")
        for service in ("nginx", "management-nginx"):
            self.assertRegex(
                compose,
                rf'(?m)^  {re.escape(service)}:\n    profiles: \["standalone-ingress"\]$',
            )
        combined = compose.split("  combined-nginx:", 1)[1].split("\nnetworks:", 1)[0]
        self.assertRegex(
            combined,
            r'\$\{HTTPS_BIND_ADDRESS:-0\.0\.0\.0\}:\$\{HTTPS_PORT:-443\}:443',
        )
        self.assertNotIn("AGENTSWATCH_MANAGEMENT_HTTPS_PORT", combined)
        self.assertIn("management-run:/data/agentswatch/management/run:ro", combined)
        self.assertIn("- default", combined)
        self.assertIn("- management-backend", combined)

    def test_backend_networks_are_separated_by_shared_nginx(self):
        compose = self.read("compose.production.example.yml")
        management_api = compose.split("  management-api:", 1)[1].split("\n  management-mcp:", 1)[0]
        management_mcp = compose.split("  management-mcp:", 1)[1].split("\n  observability-control:", 1)[0]
        adapter = compose.split("  observability-control:", 1)[1].split("\n  combined-nginx:", 1)[0]
        self.assertIn("- management-backend", management_api)
        self.assertNotIn("- default", management_api)
        self.assertIn("- management-backend", management_mcp)
        self.assertIn("- default", adapter)
        self.assertNotIn("ports:", adapter)
        self.assertIn("AGENTSWATCH_OBSERVABILITY_CONTROL_UID", adapter)
        self.assertIn("AGENTSWATCH_OBSERVABILITY_CONTROL_GID", adapter)
        self.assertIn("PGSSLMODE: verify-full", adapter)
        self.assertIn(
            "PGSSLROOTCERT: /run/secrets/agentswatch-database-ca.crt",
            adapter,
        )
        self.assertIn(
            "AGENTSWATCH_DATABASE_CA_HOST_FILE:?set AGENTSWATCH_DATABASE_CA_HOST_FILE",
            adapter,
        )
        self.assertIn(
            "AGENTSWATCH_OBSERVABILITY_CONTROL_URL: http://combined-nginx:8080",
            management_api,
        )
        self.assertIn(
            'AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY: "${AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY:-false}"',
            adapter,
        )
        self.assertIn(
            'AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY: "${AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY:-false}"',
            adapter,
        )

    def test_shared_nginx_owns_all_public_routes_and_internal_adapter_hop(self):
        nginx = self.read("nginx-combined.conf")
        self.assertIn("listen 443 ssl;", nginx)
        self.assertIn("location ^~ /agentswatch/", nginx)
        self.assertIn(
            "proxy_pass http://unix:/data/agentswatch/management/run/api.sock:/",
            nginx,
        )
        self.assertIn("location ^~ /agentswatch-mcp/", nginx)
        self.assertIn(
            "proxy_pass http://unix:/data/agentswatch/management/run/mcp.sock:/",
            nginx,
        )
        self.assertIn("proxy_pass http://grafana_backend;", nginx)
        self.assertIn("listen 8080;", nginx)
        self.assertIn("proxy_pass http://observability_control_backend;", nginx)
        self.assertEqual(1, len(re.findall(r"(?m)^\s*listen 443 ssl;", nginx)))

    def test_adapter_image_is_non_root_and_allow_listed(self):
        dockerfile = self.read("Dockerfile.observability-control")
        ignore = self.read("Dockerfile.observability-control.dockerignore")
        self.assertIn("USER agentswatch:agentswatch", dockerfile)
        self.assertNotRegex(dockerfile, r"(?m)^EXPOSE\s")
        self.assertIn("requirements-observability-control.txt", dockerfile)
        self.assertIn("agentswatch_observability", dockerfile)
        self.assertIn("scripts/generate_lobster_configs.py", dockerfile)
        self.assertTrue(ignore.startswith("**\n"))
        self.assertIn("!agentswatch_observability/**", ignore)
        self.assertIn("!scripts/generate_lobster_configs.py", ignore)
        self.assertIn("**/.env", ignore)

    def test_combined_env_contains_no_embedded_secret(self):
        env = self.read(".env.production.example")
        self.assertRegex(env, r"(?m)^COMPOSE_PROFILES=\s*$")
        for name in (
            "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL",
            "AGENTSWATCH_OTEL_ENDPOINT",
            "AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN",
        ):
            self.assertRegex(env, rf"(?m)^{name}=\s*$")
        self.assertRegex(
            env,
            r"(?m)^AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY=false$",
        )
        self.assertRegex(
            env,
            r"(?m)^AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY=false$",
        )
        self.assertNotRegex(env, r"(?i)(password|secret|token)=[^\s]+")

    def test_production_lockdown_requires_images_credentials_and_never_pulls(self):
        compose = self.read("compose.production-lockdown.yml")
        for variable in (
            "MINIO_IMAGE",
            "MINIO_MC_IMAGE",
            "OTEL_COLLECTOR_IMAGE",
            "PROMETHEUS_IMAGE",
            "ALERTMANAGER_IMAGE",
            "THANOS_IMAGE",
            "LOKI_IMAGE",
            "TEMPO_IMAGE",
            "GRAFANA_IMAGE",
            "NGINX_IMAGE",
            "CLICKHOUSE_IMAGE",
        ):
            self.assertIn(f"${{{variable}:?", compose)
        for variable in (
            "GRAFANA_ADMIN_USER",
            "GRAFANA_ADMIN_PASSWORD",
            "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD",
            "CLICKHOUSE_USER",
            "CLICKHOUSE_PASSWORD",
        ):
            self.assertIn(f"${{{variable}:?", compose)
        self.assertNotIn(":-", compose)

        service_matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):$", compose))
        self.assertGreater(len(service_matches), 20)
        for index, match in enumerate(service_matches):
            end = service_matches[index + 1].start() if index + 1 < len(service_matches) else len(compose)
            self.assertIn(
                "pull_policy: never",
                compose[match.start():end],
                f"{match.group(1)} must never pull in production",
            )

    def test_operations_production_env_has_no_secret_or_floating_image_default(self):
        env = self.read(".env.operations.production.example")
        for variable in (
            "MINIO_IMAGE",
            "MINIO_MC_IMAGE",
            "OTEL_COLLECTOR_IMAGE",
            "PROMETHEUS_IMAGE",
            "ALERTMANAGER_IMAGE",
            "THANOS_IMAGE",
            "LOKI_IMAGE",
            "TEMPO_IMAGE",
            "GRAFANA_IMAGE",
            "NGINX_IMAGE",
            "CLICKHOUSE_IMAGE",
            "GRAFANA_ADMIN_USER",
            "GRAFANA_ADMIN_PASSWORD",
            "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD",
            "CLICKHOUSE_USER",
            "CLICKHOUSE_PASSWORD",
        ):
            self.assertRegex(env, rf"(?m)^{variable}=\s*$")
        self.assertNotRegex(env, r"(?m)^[A-Z0-9_]+=.*:latest")
        self.assertNotRegex(env, r"(?i)(password|secret|token)=[^\s]+")
        self.assertRegex(env, r"(?m)^AGENTSWATCH_DATA_ROOT=/data/agentswatch$")
        self.assertRegex(env, r"(?m)^ADMIN_BIND_ADDRESS=127\.0\.0\.1$")

    def test_optional_legacy_openclaw_route_is_isolated_and_authenticated(self):
        compose = self.read("compose.legacy-openclaw-relay.yml")
        nginx = self.read("nginx-legacy-openclaw.conf")
        relay = (ROOT / "deploy" / "management" / "openclaw_loopback_relay.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("network_mode: host", compose)
        self.assertNotIn("ports:", compose)
        self.assertIn("cap_drop:\n      - ALL", compose)
        self.assertIn('AGENTSWATCH_OPENCLAW_RELAY_HOST: 127.0.0.1', compose)
        self.assertIn("management-run:/data/agentswatch/management/run", compose)
        self.assertIn("auth_basic_user_file", nginx)
        self.assertIn('proxy_set_header Authorization ""', nginx)
        self.assertIn("/data/agentswatch/management/run/openclaw.sock", nginx)
        self.assertIn("agentswatch.htpasswd:ro,z", compose)
        self.assertIn("socket.create_connection", compose)
        self.assertIn('UPSTREAM_HOST != "127.0.0.1"', relay)
        self.assertIn("refusing to replace a symlinked", relay)


if __name__ == "__main__":
    unittest.main()
