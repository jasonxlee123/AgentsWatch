import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "management"


class ManagementDeploymentTest(unittest.TestCase):
    def read(self, name: str) -> str:
        return (DEPLOY / name).read_text(encoding="utf-8")

    def test_expected_deployment_files_exist(self):
        expected = {
            "Dockerfile",
            "Dockerfile.dockerignore",
            "api_socket_launcher.py",
            "mcp_socket_launcher.py",
            "openclaw_loopback_relay.py",
            "container-entrypoint.sh",
            "compose.production.example.yml",
            "compose.test-postgres.yml",
            ".env.production.example",
            ".env.test-postgres.example",
            "agentswatch-management.service",
            "agentswatch-mcp.service",
            "agentswatch-openclaw-relay.service",
            "management.test.env.example",
            "nginx-agentswatch.conf.example",
            "nginx-agentswatch.test.conf.example",
            "README.md",
        }
        self.assertEqual(expected, {item.name for item in DEPLOY.iterdir() if item.is_file()})

    def test_image_is_multistage_subpath_build_and_single_worker(self):
        dockerfile = self.read("Dockerfile")
        self.assertGreaterEqual(len(re.findall(r"(?m)^FROM ", dockerfile)), 3)
        self.assertIn("AGENTSWATCH_FRONTEND_BASE=/agentswatch/", dockerfile)
        self.assertIn("RUN npm run build", dockerfile)
        self.assertIn("/opt/agentswatch/frontend-dist", dockerfile)
        launcher = self.read("api_socket_launcher.py")
        self.assertRegex(launcher, r'"--workers",\s*\n\s*"1"')
        self.assertIn('SOCKET_PATH = Path("/data/agentswatch/management/run/api.sock")', launcher)
        self.assertIn("SOCKET_MODE = 0o660", launcher)
        self.assertIn("sys.executable", launcher)
        self.assertIn('"-m",', launcher)
        self.assertIn('"uvicorn",', launcher)
        self.assertIn('"--fd"', launcher)
        self.assertNotIn('"--uds"', launcher)
        self.assertIn("socket.AF_UNIX", dockerfile)
        self.assertNotRegex(dockerfile, r"(?m)^EXPOSE\s")
        self.assertIn("USER agentswatch:agentswatch", dockerfile)
        self.assertNotIn("automation/artifacts", dockerfile)
        self.assertNotIn("automation/inventory", dockerfile)
        self.assertIn("requirements-mcp.txt", dockerfile)
        self.assertIn("agentswatch_mcp", dockerfile)
        self.assertIn("agentswatch-mcp-socket-launcher", dockerfile)
        self.assertIn("agentswatch-openclaw-loopback-relay", dockerfile)

    def test_build_context_is_allow_listed(self):
        ignore = self.read("Dockerfile.dockerignore")
        self.assertTrue(ignore.startswith("**\n"))
        for blocked in (
            "automation/artifacts/",
            "automation/inventory/",
            "automation/managed/",
            "frontend/node_modules/",
            "**/.env",
        ):
            self.assertIn(blocked, ignore)
        self.assertIn("!requirements-mcp.txt", ignore)
        self.assertIn("!agentswatch_mcp/**", ignore)
        self.assertIn("!deploy/management/mcp_socket_launcher.py", ignore)
        self.assertIn("!deploy/management/openclaw_loopback_relay.py", ignore)
        self.assertIn("!scripts/", ignore)
        self.assertIn("!scripts/manage_auth_user.py", ignore)

    def test_compose_has_private_api_mcp_and_management_ingress(self):
        compose = self.read("compose.production.example.yml")
        service_lines = []
        in_services = False
        for line in compose.splitlines():
            if line == "services:":
                in_services = True
                continue
            if in_services and line and not line.startswith(" "):
                break
            if in_services and re.fullmatch(r"  [A-Za-z0-9_.-]+:", line):
                service_lines.append(line.strip()[:-1])
        self.assertEqual(
            ["management-api", "management-mcp", "management-nginx"],
            service_lines,
        )
        self.assertRegex(
            compose,
            r'\$\{AGENTSWATCH_MANAGEMENT_BIND_ADDRESS:-0\.0\.0\.0\}:'
            r'\$\{AGENTSWATCH_MANAGEMENT_HTTPS_PORT:-8443\}:8443',
        )
        self.assertIn(
            "image: ${AGENTSWATCH_MANAGEMENT_NGINX_IMAGE:-nginx:stable-alpine}",
            compose,
        )
        self.assertIn(
            'AGENTSWATCH_NGINX_GID: "${AGENTSWATCH_NGINX_GID:-101}"',
            compose,
        )
        self.assertIn("PGSSLMODE: verify-full", compose)
        self.assertIn(
            "PGSSLROOTCERT: /run/secrets/agentswatch-database-ca.crt",
            compose,
        )
        self.assertIn(
            "AGENTSWATCH_DATABASE_CA_HOST_FILE:?set AGENTSWATCH_DATABASE_CA_HOST_FILE",
            compose,
        )
        self.assertNotRegex(compose, r"(?m)^networks:")
        self.assertNotIn("group_add:", compose)
        self.assertIn(
            '"${AGENTSWATCH_MANAGEMENT_DATA_HOST_ROOT:-/srv/agentswatch/management}:/data/agentswatch/management:z"',
            compose,
        )
        self.assertIn("management-run:/data/agentswatch/management/run", compose)
        self.assertIn("management-run: {}", compose)
        self.assertNotIn("external: true", compose)
        self.assertIn('AGENTSWATCH_ALLOW_LOCAL_CONNECTION: "false"', compose)
        self.assertIn("AGENTSWATCH_API_UDS: /data/agentswatch/management/run/api.sock", compose)
        self.assertIn("/usr/local/bin/agentswatch-mcp-socket-launcher", compose)
        self.assertIn("/data/agentswatch/management/run/mcp.sock", compose)
        self.assertNotRegex(
            compose.split("  management-mcp:", 1)[1],
            r"AGENTSWATCH_(?:WRITE_)?DATABASE_URL",
        )
        self.assertNotRegex(compose, r"(?m)^  (postgres|database)[^:]*:")
        self.assertIn(
            "./nginx-agentswatch.conf.example:/etc/nginx/conf.d/default.conf:ro",
            compose,
        )
        self.assertNotIn("observability", compose.split("services:", 1)[1].split("volumes:", 1)[0])

    def test_examples_do_not_embed_credentials(self):
        production_env = self.read(".env.production.example")
        test_env = self.read("management.test.env.example")
        postgres_env = self.read(".env.test-postgres.example")
        for name in (
            "AGENTSWATCH_DATABASE_CA_HOST_FILE",
            "AGENTSWATCH_DATABASE_URL",
            "AGENTSWATCH_WRITE_DATABASE_URL",
            "AGENTSWATCH_WEB_CREDENTIAL_KEY",
            "AGENTSWATCH_SSH_CREDENTIAL_KEY",
        ):
            self.assertRegex(production_env, rf"(?m)^{name}=\s*$")
            if name != "AGENTSWATCH_DATABASE_CA_HOST_FILE":
                self.assertRegex(test_env, rf"(?m)^{name}=\s*$")
        self.assertNotRegex(production_env, r"(?i)(password|secret|token)=[^\s]+")
        for name in (
            "AGENTSWATCH_TEST_POSTGRES_DB",
            "AGENTSWATCH_TEST_POSTGRES_USER",
            "AGENTSWATCH_TEST_POSTGRES_PASSWORD",
        ):
            self.assertRegex(postgres_env, rf"(?m)^{name}=\s*$")

    def test_direct_service_is_unix_socket_and_single_worker(self):
        unit = self.read("agentswatch-management.service")
        self.assertNotIn("--host ", unit)
        self.assertIn("api_socket_launcher.py", unit)
        self.assertNotIn("SupplementaryGroups", unit)
        self.assertIn("UMask=0007", unit)
        self.assertIn("EnvironmentFile=/etc/agentswatch/management.env", unit)
        self.assertIn("WorkingDirectory=/opt/agentswatch-management", unit)
        self.assertIn("ExecStart=/opt/agentswatch-management/.venv/bin/python", unit)
        self.assertIn("ReadWritePaths=/data/agentswatch/management", unit)

        mcp_unit = self.read("agentswatch-mcp.service")
        self.assertNotIn("--host ", mcp_unit)
        self.assertIn("mcp_socket_launcher.py", mcp_unit)
        self.assertIn("EnvironmentFile=/etc/agentswatch/management.env", mcp_unit)
        self.assertIn("ReadWritePaths=/data/agentswatch/management/run", mcp_unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", mcp_unit)

        mcp_launcher = self.read("mcp_socket_launcher.py")
        self.assertIn('SOCKET_PATH = Path("/data/agentswatch/management/run/mcp.sock")', mcp_launcher)
        self.assertIn("SOCKET_MODE = 0o660", mcp_launcher)
        self.assertIn('"agentswatch_mcp.server:app"', mcp_launcher)
        self.assertRegex(mcp_launcher, r'"--workers",\s*\n\s*"1"')
        self.assertIn('"--fd"', mcp_launcher)
        self.assertNotIn('"--uds"', mcp_launcher)

    def test_entrypoint_rejects_path_escape(self):
        entrypoint = self.read("container-entrypoint.sh")
        self.assertIn("reject_parent_segments", entrypoint)
        self.assertIn("realpath -e", entrypoint)
        self.assertIn("realpath -m", entrypoint)
        self.assertIn('"${data_root}"/*', entrypoint)
        self.assertIn('expected AgentsWatch UID 10001', entrypoint)
        self.assertIn('expected setgid mode 2750', entrypoint)
        self.assertNotIn('chmod 2750', entrypoint)

    def test_uds_volume_initialization_restores_only_required_capabilities(self):
        compose = self.read("compose.production.example.yml")
        api_service = compose.split("  management-api:", 1)[1].split(
            "  management-mcp:", 1
        )[0]
        self.assertIn("cap_drop:\n      - ALL", api_service)

        initialization = (
            "run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add FOWNER "
            "--cap-add FSETID --entrypoint sh management-api"
        )
        unsafe_initialization = (
            "run --rm --no-deps --user 0:0 --entrypoint sh management-api"
        )
        readme = self.read("README.md")
        guide = (ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
        self.assertEqual(readme.count(initialization), 1)
        self.assertEqual(guide.count(initialization), 2)
        self.assertNotIn(unsafe_initialization, readme)
        self.assertNotIn(unsafe_initialization, guide)

    def test_management_ingress_owns_only_management_routes(self):
        production = self.read("nginx-agentswatch.conf.example")
        local_test = self.read("nginx-agentswatch.test.conf.example")
        for nginx in (production, local_test):
            self.assertIn("location ^~ /agentswatch/", nginx)
            self.assertIn("proxy_pass http://unix:/data/agentswatch/management/run/api.sock:/", nginx)
            self.assertIn("location ^~ /agentswatch-mcp/", nginx)
            self.assertIn("proxy_pass http://unix:/data/agentswatch/management/run/mcp.sock:/", nginx)
            self.assertIn('proxy_set_header Authorization $http_authorization', nginx)
            self.assertIn('proxy_set_header Host 127.0.0.1:8010', nginx)
            self.assertIn('proxy_set_header Origin http://127.0.0.1:8010', nginx)
            self.assertIn("proxy_buffering off", nginx)
            self.assertNotIn("location ^~ /openclaw/", nginx)
            self.assertNotIn("runtime-web/openclaw.sock", nginx)
            self.assertNotRegex(nginx, r"proxy_pass\s+https?://[^;]*grafana")
            self.assertIn("listen ", nginx)
            self.assertNotIn("alias ", nginx)
            self.assertNotIn("auth_basic", nginx)
        self.assertIn("listen 8443 ssl", production)
        self.assertIn("/etc/agentswatch-management/tls/tls.crt", production)
        self.assertIn("listen 8080", local_test)

    def test_api_serves_the_built_frontend(self):
        dockerfile = self.read("Dockerfile")
        test_env = self.read("management.test.env.example")
        self.assertIn("AGENTSWATCH_STATIC_DIR=/opt/agentswatch/frontend-dist", dockerfile)
        self.assertIn("AGENTSWATCH_STATIC_DIR=/data/agentswatch/management/frontend", test_env)
        self.assertIn("AGENTSWATCH_ALLOW_LOCAL_CONNECTION=false", test_env)
        self.assertNotIn("AGENTSWATCH_ALLOW_LOCAL_CONNECTION=true", test_env)
        self.assertIn("AGENTSWATCH_SESSION_COOKIE_SECURE=true", test_env)
        self.assertIn("AGENTSWATCH_SESSION_COOKIE_PATH=/agentswatch/", test_env)

    def test_test_postgres_is_isolated_and_loopback_only(self):
        compose = self.read("compose.test-postgres.yml")
        service_lines = []
        in_services = False
        for line in compose.splitlines():
            if line == "services:":
                in_services = True
                continue
            if in_services and line and not line.startswith(" "):
                break
            if in_services and re.fullmatch(r"  [A-Za-z0-9_.-]+:", line):
                service_lines.append(line.strip()[:-1])
        self.assertEqual(["management-postgres"], service_lines)
        self.assertIn("image: postgres:16-alpine", compose)
        self.assertIn('"127.0.0.1:${AGENTSWATCH_POSTGRES_PORT:-55432}:5432"', compose)
        self.assertIn("/data/agentswatch/management/postgres:/var/lib/postgresql/data", compose)
        self.assertIn("test-postgres:", compose)
        self.assertNotIn("external:", compose)
        self.assertNotIn("observability", compose)
        for name in (
            "AGENTSWATCH_TEST_POSTGRES_DB",
            "AGENTSWATCH_TEST_POSTGRES_USER",
            "AGENTSWATCH_TEST_POSTGRES_PASSWORD",
        ):
            self.assertIn(f"${{{name}:?", compose)

    def test_openclaw_relay_is_unix_socket_only(self):
        unit = self.read("agentswatch-openclaw-relay.service")
        self.assertIn("User=openclaw", unit)
        self.assertIn("Group=openclaw", unit)
        self.assertNotIn("SupplementaryGroups", unit)
        self.assertIn("/usr/bin/socat UNIX-LISTEN:", unit)
        self.assertIn("runtime-web/openclaw.sock", unit)
        self.assertIn("TCP:127.0.0.1:16402", unit)
        self.assertIn("UMask=0007", unit)

    def test_direct_test_requirements_support_ubuntu_2204_python(self):
        for name in ("requirements-api.txt", "requirements-automation.txt"):
            requirements = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                'ansible-core>=2.18,<2.20; python_version >= "3.11"',
                requirements,
            )
            self.assertIn(
                'ansible-core==2.17.14; python_version < "3.11"',
                requirements,
            )


if __name__ == "__main__":
    unittest.main()
