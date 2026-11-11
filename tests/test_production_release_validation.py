import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_production_release as production  # noqa: E402


class ProductionReleaseValidationTests(unittest.TestCase):
    def application_model(self):
        services = {
            name: {"image": f"example/{name}:2.0.1", "networks": {"default": None}}
            for name in production.REQUIRED_APPLICATION_IMAGES
        }
        services["nginx"] = {
            "image": "nginx:1.29.0-alpine",
            "profiles": ["standalone-ingress"],
            "ports": [{"host_ip": "0.0.0.0", "published": "443", "target": 443}],
        }
        services["management-nginx"] = {
            "image": "nginx:1.29.0-alpine",
            "profiles": ["standalone-ingress"],
            "ports": [{"host_ip": "0.0.0.0", "published": "8443", "target": 8443}],
        }
        services["combined-nginx"]["ports"] = [
            {"host_ip": "0.0.0.0", "published": "443", "target": 443}
        ]
        services["otel-collector"]["ports"] = [
            {"host_ip": "0.0.0.0", "published": "4318", "target": 4318},
            {"host_ip": "127.0.0.1", "published": "13133", "target": 13133},
        ]
        services["minio"]["environment"] = {
            "MINIO_ROOT_USER": "minioadmin",
            "MINIO_ROOT_PASSWORD": "site-minio-secret",
        }
        services["clickhouse"]["environment"] = {
            "CLICKHOUSE_USER": "otel",
            "CLICKHOUSE_PASSWORD": "site-clickhouse-secret",
        }
        services["grafana"]["environment"] = {
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "site-grafana-secret",
        }
        services["management-api"]["environment"] = {
            "AGENTSWATCH_DATABASE_URL": "postgresql://reader:secret@agentswatch-db:5432/agentswatch?sslmode=verify-full",
            "AGENTSWATCH_WRITE_DATABASE_URL": "postgresql://writer:secret@agentswatch-db:5432/agentswatch?sslmode=verify-full",
        }
        services["management-api"]["networks"] = {
            "management-backend": None,
            production.DATABASE_NETWORK: None,
        }
        services["observability-control"]["environment"] = {
            "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL": "postgresql://observer:secret@agentswatch-db:5432/agentswatch?sslmode=verify-full",
            "AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY": "false",
            "AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY": "false",
        }
        services["observability-control"]["networks"] = {
            "default": None,
            production.DATABASE_NETWORK: None,
        }
        for name, (target, source) in production.APPLICATION_DATA_MOUNTS.items():
            services[name].setdefault("volumes", []).append(
                {"type": "bind", "source": source, "target": target}
            )
        return {
            "services": services,
            "networks": {
                "default": {},
                "management-backend": {},
                production.DATABASE_NETWORK: {"external": True, "name": production.DATABASE_NETWORK},
            },
        }

    def postgres_model(self):
        return {
            "services": {
                "postgres": {
                    "image": "postgres:16-alpine",
                    "networks": {production.DATABASE_NETWORK: None},
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/data/agentswatch/management/postgres",
                            "target": "/var/lib/postgresql/data",
                        }
                    ],
                }
            },
            "networks": {
                production.DATABASE_NETWORK: {
                    "internal": True,
                    "name": production.DATABASE_NETWORK,
                }
            },
        }

    def test_valid_application_and_postgres_models(self):
        images = production.validate(self.application_model())
        self.assertIn("example/management-api:2.0.1", images)
        self.assertEqual(production.validate(self.postgres_model()), ["postgres:16-alpine"])

    def test_optional_legacy_relay_is_validated_when_present(self):
        model = self.application_model()
        model["services"]["legacy-openclaw-relay"] = {
            "image": "agentswatch-management:latest",
            "network_mode": "host",
        }
        with self.assertRaisesRegex(production.ValidationError, "legacy-openclaw-relay"):
            production.validate(model)

    def test_rejects_mutable_or_placeholder_image(self):
        for image in ("grafana/grafana:latest", "registry.example.invalid/app:2.0.1", "grafana/grafana"):
            with self.subTest(image=image):
                model = self.application_model()
                model["services"]["grafana"]["image"] = image
                with self.assertRaisesRegex(production.ValidationError, "pinned"):
                    production.validate(model)

    def test_rejects_weak_password_without_printing_it(self):
        model = self.application_model()
        model["services"]["minio"]["environment"]["MINIO_ROOT_PASSWORD"] = "minioadmin"
        stdin = io.StringIO(json.dumps(model))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(production.main([]), 1)
        self.assertNotIn("minioadmin", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_rejects_database_host_tls_and_destructive_overrides(self):
        mutations = (
            ("AGENTSWATCH_DATABASE_URL", "postgresql://reader:secret@127.0.0.1/agentswatch?sslmode=verify-full"),
            ("AGENTSWATCH_DATABASE_URL", "postgresql://reader:secret@agentswatch-db/agentswatch?sslmode=disable"),
        )
        for key, value in mutations:
            with self.subTest(value=value):
                model = self.application_model()
                model["services"]["management-api"]["environment"][key] = value
                with self.assertRaises(production.ValidationError):
                    production.validate(model)
        model = self.application_model()
        model["services"]["observability-control"]["environment"][
            "AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY"
        ] = "true"
        with self.assertRaisesRegex(production.ValidationError, "must remain false"):
            production.validate(model)

    def test_allows_external_tls_database_without_local_network(self):
        model = self.application_model()
        del model["networks"][production.DATABASE_NETWORK]
        for name in ("management-api", "observability-control"):
            del model["services"][name]["networks"][production.DATABASE_NETWORK]
        for key in ("AGENTSWATCH_DATABASE_URL", "AGENTSWATCH_WRITE_DATABASE_URL"):
            model["services"]["management-api"]["environment"][key] = (
                "postgresql://role:secret@db.example.internal:5432/agentswatch?sslmode=verify-full"
            )
        model["services"]["observability-control"]["environment"][
            "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL"
        ] = "postgresql://observer:secret@db.example.internal:5432/agentswatch?sslmode=verify-full"
        production.validate(model)

    def test_rejects_database_network_drift(self):
        model = self.application_model()
        model["services"]["management-mcp"]["networks"][production.DATABASE_NETWORK] = None
        with self.assertRaisesRegex(production.ValidationError, "members"):
            production.validate(model)
        model = self.postgres_model()
        model["networks"][production.DATABASE_NETWORK]["internal"] = False
        with self.assertRaisesRegex(production.ValidationError, "internal"):
            production.validate(model)

    def test_rejects_postgres_or_public_port_drift(self):
        model = self.postgres_model()
        model["services"]["postgres"]["ports"] = [
            {"host_ip": "127.0.0.1", "published": "55432", "target": 5432}
        ]
        with self.assertRaisesRegex(production.ValidationError, "must not publish"):
            production.validate(model)
        model = self.application_model()
        model["services"]["grafana"]["ports"] = [
            {"host_ip": "0.0.0.0", "published": "3000", "target": 3000}
        ]
        with self.assertRaisesRegex(production.ValidationError, "public listeners"):
            production.validate(model)

    def test_rejects_data_mount_outside_production_root(self):
        model = self.application_model()
        model["services"]["prometheus"]["volumes"][0]["source"] = "/tmp/prometheus"
        with self.assertRaisesRegex(production.ValidationError, "production /data path"):
            production.validate(model)

    def test_rejects_standalone_ingress_activation(self):
        model = self.application_model()
        model["services"]["nginx"].pop("profiles")
        with self.assertRaisesRegex(production.ValidationError, "standalone-ingress"):
            production.validate(model)

    def test_accepts_omitted_inactive_standalone_ingresses(self):
        model = self.application_model()
        del model["services"]["nginx"]
        del model["services"]["management-nginx"]
        production.validate(model)

    def test_image_record_is_exclusive_and_mode_0600(self):
        inspection = json.dumps(
            [{"Id": "sha256:abc", "Architecture": "amd64", "Os": "linux", "RepoDigests": []}]
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "images.json"
            with mock.patch.object(
                production.subprocess,
                "run",
                return_value=mock.Mock(stdout=inspection),
            ):
                production._inspect_images(["example/app:2.0.1"], output)
                with self.assertRaises(FileExistsError):
                    production._inspect_images(["example/app:2.0.1"], output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text())["images"][0]["id"], "sha256:abc")

    def test_image_record_rejects_wrong_platform(self):
        inspection = json.dumps(
            [{"Id": "sha256:abc", "Architecture": "arm64", "Os": "linux", "RepoDigests": []}]
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            production.subprocess,
            "run",
            return_value=mock.Mock(stdout=inspection),
        ):
            with self.assertRaisesRegex(production.ValidationError, "linux/amd64"):
                production._inspect_images(
                    ["example/app:2.0.1"], Path(directory) / "images.json"
                )


if __name__ == "__main__":
    unittest.main()
