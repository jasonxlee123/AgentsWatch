import argparse
import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load_module("generate_lobster_configs", ROOT / "scripts" / "generate_lobster_configs.py")
lobsterctl = load_module("lobsterctl", ROOT / "scripts" / "lobsterctl.py")


class ScrapeConfigTests(unittest.TestCase):
    def test_each_gateway_has_an_individual_job_and_token_file(self):
        items = [
            {
                "gateway_id": "lobster-001",
                "host": "10.0.0.1",
                "port": 16402,
                "cluster": "lobster-prod",
                "lobster_name": "First",
                "runtime_id": "runtime-001",
                "server_id": "server-001",
                "server_display_id": "ECS-001",
            },
            {
                "gateway_id": "lobster-002",
                "host": "10.0.0.2",
                "port": 16402,
                "cluster": "lobster-prod",
                "lobster_name": "Second",
            },
        ]

        rendered = generator.render_scrape_configs(items)

        self.assertTrue(rendered.startswith("scrape_configs:\n"))
        self.assertEqual(rendered.count("- job_name:"), 2)
        self.assertIn("openclaw-gateway-lobster-001", rendered)
        self.assertIn("openclaw-gateway-lobster-002", rendered)
        self.assertIn("/etc/prometheus/secrets/gateways/lobster-001.token", rendered)
        self.assertIn("/etc/prometheus/secrets/gateways/lobster-002.token", rendered)
        self.assertEqual(rendered.count('replacement: "openclaw-gateways"'), 2)
        self.assertEqual(rendered.count('native_scrape_expected: "false"'), 2)
        self.assertIn('runtime_id: "runtime-001"', rendered)
        self.assertIn('server_display_id: "ECS-001"', rendered)

    def test_legacy_metadata_server_id_is_promoted_to_display_label(self):
        item = generator.merged(
            {"otel_endpoint": "http://10.0.0.2:4318"},
            {
                "gateway_id": "lobster-001",
                "host": "10.0.0.1",
                "metadata": {"server_id": "ECS-001"},
            },
        )

        self.assertEqual(item["server_display_id"], "ECS-001")

    def test_monitoring_patch_enables_lan_bind_without_touching_auth_or_port(self):
        item = {
            "gateway_id": "lobster-001",
            "runtime_id": "00000000-0000-4000-8000-000000000701",
            "server_id": "00000000-0000-4000-8000-000000000501",
            "host": "10.0.0.1",
            "otel_endpoint": "http://10.0.0.2:4318",
            "gateway_bind": "lan",
        }

        patch = json.loads(generator.render_otel_patch(item))

        self.assertEqual(patch["gateway"], {"bind": "lan"})
        self.assertNotIn("auth", patch["gateway"])
        self.assertNotIn("port", patch["gateway"])
        self.assertTrue(patch["plugins"]["entries"]["diagnostics-otel"]["enabled"])
        self.assertTrue(patch["plugins"]["entries"]["diagnostics-prometheus"]["enabled"])
        self.assertEqual(
            patch["diagnostics"]["otel"]["headers"]["x-agentswatch-runtime-id"],
            item["runtime_id"],
        )
        self.assertEqual(
            patch["diagnostics"]["otel"]["headers"]["x-agentswatch-server-id"],
            item["server_id"],
        )

    def test_lan_bind_marks_native_scrape_as_expected(self):
        item = generator.merged(
            {"otel_endpoint": "http://10.0.0.2:4318"},
            {
                "gateway_id": "lobster-001",
                "host": "10.0.0.1",
                "gateway_bind": "lan",
            },
        )

        rendered = generator.render_scrape_configs([item])

        self.assertIn('native_scrape_expected: "true"', rendered)

    def test_monitoring_patch_leaves_gateway_network_unchanged_by_default(self):
        item = {
            "gateway_id": "lobster-001",
            "host": "10.0.0.1",
            "otel_endpoint": "http://10.0.0.2:4318",
        }

        patch = json.loads(generator.render_otel_patch(item))

        self.assertNotIn("gateway", patch)

    def test_otel_headers_only_carry_stable_ascii_identity_ids(self):
        item = {
            "gateway_id": "lobster-001",
            "runtime_id": "00000000-0000-4000-8000-000000000701",
            "server_id": "00000000-0000-4000-8000-000000000501",
            "primary_agent_id": "00000000-0000-4000-8000-000000000901",
            "agent_deployment_id": "00000000-0000-4000-8000-000000001001",
            "host": "10.0.0.1",
            "otel_endpoint": "http://10.0.0.2:4318",
            "cluster": "生产集群",
            "env": "生产",
            "region": "华东",
            "team": "智能体平台团队",
            "deployment_mode": "裸金属",
        }

        headers = json.loads(generator.render_otel_patch(item))["diagnostics"]["otel"]["headers"]

        self.assertEqual(
            set(headers),
            {
                "x-openclaw-gateway-id",
                "x-agentswatch-runtime-id",
                "x-agentswatch-server-id",
                "x-agentswatch-primary-agent-id",
                "x-agentswatch-agent-deployment-id",
            },
        )

    def test_invalid_gateway_bind_is_rejected(self):
        defaults = {"otel_endpoint": "http://10.0.0.2:4318"}
        gateway = {
            "gateway_id": "lobster-001",
            "host": "10.0.0.1",
            "gateway_bind": "0.0.0.0",
        }

        with self.assertRaisesRegex(ValueError, "gateway_bind must be one of"):
            generator.merged(defaults, gateway)


FAKE_JSON5_MODULE = r"""
function parse(text) {
  const normalized = text
    .replace(/\/\/[^\n]*/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*)\s*:/g, '$1"$2":')
    .replace(/'([^'\\]*)'/g, '"$1"')
    .replace(/,\s*([}\]])/g, "$1");
  return JSON.parse(normalized);
}
module.exports = { parse };
"""


class GatewayTokenTests(unittest.TestCase):
    def test_token_copy_is_written_with_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateways" / "lobster-001.token"

            lobsterctl.write_gateway_token(path, "existing-secret\n")

            self.assertEqual(path.read_text(), "existing-secret")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertIn(stat.S_IMODE(path.parent.stat().st_mode), {0o2750, 0o750})

    def test_empty_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateways" / "lobster-001.token"
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                lobsterctl.write_gateway_token(path, "  \n")

    def test_token_collect_resumes_by_skipping_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            secrets = root / "secrets"
            inventory.write_text(
                json.dumps(
                    {
                        "gateways": [
                            {"gateway_id": "lobster-001", "host": "10.0.0.1"},
                            {"gateway_id": "lobster-002", "host": "10.0.0.2"},
                        ]
                    }
                )
            )
            lobsterctl.write_gateway_token(secrets / "lobster-001.token", "first-existing")
            args = argparse.Namespace(inventory=inventory, secrets_dir=secrets, overwrite=False)

            with mock.patch.object(lobsterctl.getpass, "getpass", return_value="second-existing") as prompt:
                result = lobsterctl.command_token_collect(args)

            self.assertEqual(result, 0)
            prompt.assert_called_once()
            self.assertEqual((secrets / "lobster-001.token").read_text(), "first-existing")
            self.assertEqual((secrets / "lobster-002.token").read_text(), "second-existing")

    def test_token_status_rejects_insecure_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            secrets = root / "secrets"
            inventory.write_text(json.dumps({"gateways": [{"gateway_id": "lobster-001"}]}))
            path = secrets / "lobster-001.token"
            lobsterctl.write_gateway_token(path, "existing-secret")
            path.chmod(0o644)
            args = argparse.Namespace(inventory=inventory, secrets_dir=secrets)

            self.assertEqual(lobsterctl.command_token_status(args), 1)

    def test_enable_rejects_missing_token_without_changing_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            inventory.write_text(
                json.dumps({"gateways": [{"gateway_id": "lobster-001", "enabled": False}]})
            )
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=root / "secrets",
                gateway_id="lobster-001",
                render=False,
            )

            with self.assertRaisesRegex(ValueError, "token file is missing or insecure"):
                lobsterctl.set_enabled(args, True)

            self.assertFalse(json.loads(inventory.read_text())["gateways"][0]["enabled"])

    def test_token_fetch_reads_existing_token_over_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            secrets = root / "secrets"
            env_file = root / ".env"
            known_hosts = root / "known_hosts"
            identity_file = root / "id_ed25519"
            inventory.write_text(
                json.dumps(
                    {
                        "gateways": [
                            {
                                "gateway_id": "lobster-001",
                                "host": "10.0.0.1",
                                "deployment_mode": "baremetal",
                            }
                        ]
                    }
                )
            )
            known_hosts.write_text("10.0.0.1 ssh-ed25519 AAAAC3Nza-test-only\n")
            identity_file.write_text("test-private-key-material\n")
            env_file.write_text(
                "LOBSTER_SSH_USER=openclaw\n"
                f"LOBSTER_SSH_KNOWN_HOSTS={known_hosts}\n"
                f"LOBSTER_SSH_IDENTITY_FILE={identity_file}\n"
            )
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=secrets,
                env_file=env_file,
                known_hosts=None,
                identity_file=None,
                gateway_id=None,
                ssh_user=None,
                ssh_port=None,
                openclaw_user=None,
                openclaw_config_path=None,
                container_user=None,
                connect_timeout=None,
                overwrite=False,
                dry_run=False,
            )
            completed = lobsterctl.subprocess.CompletedProcess(
                args=[], returncode=0, stdout="existing-gateway-token\n", stderr=""
            )

            with mock.patch.object(lobsterctl, "run_ssh_command", return_value=completed) as run:
                result = lobsterctl.command_token_fetch(args)

            self.assertEqual(result, 0)
            (command,) = run.call_args.args
            rendered_command = " ".join(command)
            self.assertIn("openclaw@10.0.0.1", command)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn(f"UserKnownHostsFile={known_hosts}", command)
            self.assertIn("GlobalKnownHostsFile=/dev/null", command)
            self.assertIn("UpdateHostKeys=no", command)
            self.assertIn("BatchMode=yes", command)
            self.assertIn("PreferredAuthentications=publickey", command)
            self.assertIn("PasswordAuthentication=no", command)
            self.assertIn("KbdInteractiveAuthentication=no", command)
            self.assertIn("NumberOfPasswordPrompts=0", command)
            self.assertIn("IdentitiesOnly=yes", command)
            self.assertIn(str(identity_file), command)
            self.assertNotIn("StrictHostKeyChecking=accept-new", command)
            self.assertIn('exec "$node_bin" -e', rendered_command)
            self.assertIn("/home/openclaw/.openclaw/openclaw.json", rendered_command)
            self.assertNotIn("openclaw config get gateway.auth.token", rendered_command)
            self.assertNotIn("existing-gateway-token", rendered_command)
            self.assertEqual((secrets / "lobster-001.token").read_text(), "existing-gateway-token")

    def test_container_token_fetch_uses_read_only_config_command(self):
        gateway = {
            "gateway_id": "lobster-container-001",
            "deployment_mode": "container",
            "container_name": "openclaw",
        }
        args = argparse.Namespace(container_user="node", openclaw_user=None)

        command = lobsterctl.remote_token_command(gateway, args)

        self.assertTrue(command.startswith("docker exec --user node openclaw sh -c "))
        self.assertIn('exec "$node_bin" -e', command)
        self.assertIn("/home/openclaw/.openclaw/openclaw.json", command)
        self.assertNotIn("openclaw config get", command)

    def test_read_token_command_extracts_literal_value_from_openclaw_json(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "openclaw.json"
            config_path.write_text(
                json.dumps({"gateway": {"auth": {"token": "literal-gateway-token"}}})
            )
            gateway = {"gateway_id": "lobster-local", "deployment_mode": "baremetal"}
            args = argparse.Namespace(container_user=None, openclaw_user=None)
            command = lobsterctl.remote_token_command(gateway, args, str(config_path))

            result = subprocess.run(
                ["sh", "-c", command],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "literal-gateway-token")

    def test_read_token_command_accepts_openclaw_json5(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "openclaw.json"
            config_path.write_text(
                "{ gateway: { auth: { token: 'json5-gateway-token', }, }, } // managed config\n"
            )
            # The resolver deliberately borrows json5 from the OpenClaw
            # installation. Provide a self-contained fake installation so this
            # test does not depend on OpenClaw being installed on the runner.
            fake_openclaw = Path(directory) / "fake-openclaw"
            json5_module = fake_openclaw / "node_modules" / "json5"
            json5_module.mkdir(parents=True)
            (json5_module / "index.js").write_text(FAKE_JSON5_MODULE, encoding="utf-8")
            executable = fake_openclaw / "openclaw"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)

            gateway = {"gateway_id": "lobster-json5", "deployment_mode": "baremetal"}
            args = argparse.Namespace(container_user=None, openclaw_user=None)
            command = lobsterctl.remote_token_command(gateway, args, str(config_path))
            env = os.environ.copy()
            env["OPENCLAW_BIN"] = str(executable)

            result = subprocess.run(
                ["sh", "-c", command],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "json5-gateway-token")

    def test_read_token_command_finds_node_with_minimal_ssh_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "openclaw.json"
            config_path.write_text(
                json.dumps({"gateway": {"auth": {"token": "minimal-path-token"}}})
            )
            gateway = {"gateway_id": "lobster-minimal-path", "deployment_mode": "baremetal"}
            args = argparse.Namespace(container_user=None, openclaw_user=None)
            command = lobsterctl.remote_token_command(gateway, args, str(config_path))
            env = os.environ.copy()
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"

            result = subprocess.run(
                ["sh", "-c", command],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "minimal-path-token")

    def test_token_fetch_dry_run_rejects_container_without_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            inventory.write_text(
                json.dumps(
                    {
                        "gateways": [
                            {
                                "gateway_id": "lobster-container-invalid",
                                "host": "10.0.0.9",
                                "deployment_mode": "container",
                            }
                        ]
                    }
                )
            )
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=root / "secrets",
                env_file=root / ".env",
                known_hosts=root / "known_hosts",
                identity_file=root / "id_ed25519",
                gateway_id=None,
                ssh_user=None,
                ssh_port=None,
                openclaw_user=None,
                openclaw_config_path=None,
                container_user=None,
                connect_timeout=None,
                overwrite=False,
                dry_run=True,
            )
            args.known_hosts.write_text("10.0.0.9 ssh-ed25519 AAAAC3Nza-test-only\n")
            args.identity_file.write_text("test-private-key-material\n")

            with contextlib.redirect_stderr(io.StringIO()):
                result = lobsterctl.command_token_fetch(args)

            self.assertEqual(result, 1)

    def test_ssh_command_does_not_inherit_askpass(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_ssh = Path(directory) / "fake-ssh"
            fake_ssh.write_text('#!/bin/sh\nprintf "%s\\n" "${SSH_ASKPASS-unset}"\n')
            fake_ssh.chmod(0o700)
            command = [str(fake_ssh), "openclaw@10.0.0.1", "read existing gateway token"]

            with mock.patch.dict(
                lobsterctl.os.environ,
                {"SSH_ASKPASS": "/tmp/should-not-be-used", "SSH_ASKPASS_REQUIRE": "force"},
            ):
                result = lobsterctl.run_ssh_command(command)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "unset")

    def test_token_fetch_requires_non_empty_pinned_known_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            inventory.write_text(
                json.dumps({"gateways": [{"gateway_id": "lobster-001", "host": "10.0.0.1"}]})
            )
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=root / "secrets",
                env_file=root / ".env",
                known_hosts=root / "empty-known-hosts",
                identity_file=None,
                gateway_id=None,
                ssh_user=None,
                ssh_port=None,
                openclaw_user=None,
                openclaw_config_path=None,
                container_user=None,
                connect_timeout=None,
                overwrite=False,
                dry_run=True,
            )
            args.known_hosts.touch()

            with self.assertRaisesRegex(ValueError, "known_hosts file must be a non-empty"):
                lobsterctl.command_token_fetch(args)

    def test_token_fetch_requires_explicit_identity_or_ssh_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            known_hosts = root / "known_hosts"
            inventory.write_text(
                json.dumps({"gateways": [{"gateway_id": "lobster-001", "host": "10.0.0.1"}]})
            )
            known_hosts.write_text("10.0.0.1 ssh-ed25519 AAAAC3Nza-test-only\n")
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=root / "secrets",
                env_file=root / ".env",
                known_hosts=known_hosts,
                identity_file=None,
                gateway_id=None,
                ssh_user=None,
                ssh_port=None,
                openclaw_user=None,
                openclaw_config_path=None,
                container_user=None,
                connect_timeout=None,
                overwrite=False,
                dry_run=True,
            )

            with mock.patch.dict(lobsterctl.os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "or an active SSH agent"):
                    lobsterctl.command_token_fetch(args)

    def test_token_fetch_can_use_only_the_selected_ssh_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            known_hosts = root / "known_hosts"
            inventory.write_text(
                json.dumps({"gateways": [{"gateway_id": "lobster-001", "host": "10.0.0.1"}]})
            )
            known_hosts.write_text("10.0.0.1 ssh-ed25519 AAAAC3Nza-test-only\n")
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=root / "secrets",
                env_file=root / ".env",
                known_hosts=known_hosts,
                identity_file=None,
                gateway_id=None,
                ssh_user=None,
                ssh_port=None,
                openclaw_user=None,
                openclaw_config_path=None,
                container_user=None,
                connect_timeout=None,
                overwrite=False,
                dry_run=True,
            )

            output = io.StringIO()
            with mock.patch.dict(
                lobsterctl.os.environ,
                {"SSH_AUTH_SOCK": "/tmp/agentswatch-test-agent.sock"},
                clear=True,
            ):
                with contextlib.redirect_stdout(output):
                    result = lobsterctl.command_token_fetch(args)

            self.assertEqual(result, 0)
            rendered = output.getvalue()
            self.assertIn("IdentityAgent=/tmp/agentswatch-test-agent.sock", rendered)
            self.assertIn("IdentityFile=none", rendered)
            self.assertNotIn(" -i ", rendered)

    def test_token_fetch_dry_run_lists_gateway_with_existing_local_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "lobsters.json"
            secrets = root / "secrets"
            known_hosts = root / "known_hosts"
            identity_file = root / "id_ed25519"
            inventory.write_text(
                json.dumps(
                    {
                        "gateways": [
                            {
                                "gateway_id": "lobster-existing",
                                "host": "10.0.0.8",
                                "deployment_mode": "baremetal",
                            }
                        ]
                    }
                )
            )
            lobsterctl.write_gateway_token(secrets / "lobster-existing.token", "existing-token")
            known_hosts.write_text("10.0.0.8 ssh-ed25519 AAAAC3Nza-test-only\n")
            identity_file.write_text("test-private-key-material\n")
            args = argparse.Namespace(
                inventory=inventory,
                secrets_dir=secrets,
                env_file=root / ".env",
                known_hosts=known_hosts,
                identity_file=identity_file,
                gateway_id=None,
                ssh_user=None,
                ssh_port=None,
                openclaw_user=None,
                openclaw_config_path=None,
                container_user=None,
                connect_timeout=None,
                overwrite=False,
                dry_run=True,
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = lobsterctl.command_token_fetch(args)

            self.assertEqual(result, 0)
            self.assertIn("lobster-existing", output.getvalue())
            self.assertIn("openclaw@10.0.0.8", output.getvalue())

    def test_token_fetch_script_has_no_password_or_host_key_learning_fallback(self):
        script = (ROOT / "scripts" / "lobsterctl.py").read_text()

        self.assertNotIn("LOBSTER_SSH_PASSWORD", script)
        self.assertNotIn("SSH_ASKPASS", script)
        self.assertNotIn("StrictHostKeyChecking=accept-new", script)
        self.assertIn('"StrictHostKeyChecking=yes"', script)
        self.assertIn('"PasswordAuthentication=no"', script)

    def test_apply_config_remote_dry_run_requires_pinned_key_only_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "ssh.log"
            fake_ssh = fake_bin / "ssh"
            fake_ssh.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf 'askpass=%s\\n' \"${SSH_ASKPASS-unset}\" >> \"$FAKE_SSH_LOG\"\n"
                "printf 'args=%s\\n' \"$*\" >> \"$FAKE_SSH_LOG\"\n"
                "cat >/dev/null\n"
            )
            fake_ssh.chmod(0o700)
            (root / ".env").write_text(
                "LOBSTER_SSH_PASSWORD=test-server-password\n"
                "LOBSTER_SSH_CONNECT_TIMEOUT=7\n"
            )
            known_hosts = root / "known_hosts"
            known_hosts.write_text("10.0.0.1 ssh-ed25519 AAAAC3Nza-test-only\n")
            patches = root / "patches"
            patches.mkdir()
            (patches / "lobster-001.json").write_text("{}\n")
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_SSH_LOG"] = str(log)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "apply_lobster_config.sh"),
                    "lobster-001",
                    "--ssh",
                    "openclaw@10.0.0.1",
                    "--known-hosts",
                    str(known_hosts),
                    "--connect-timeout",
                    "7",
                    "--patch-dir",
                    str(patches),
                    "--dry-run",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text()
            self.assertEqual(calls.count("askpass=unset"), 3)
            self.assertEqual(calls.count("args="), 3)
            self.assertIn("ConnectTimeout=7", calls)
            self.assertIn("StrictHostKeyChecking=yes", calls)
            self.assertIn(f"UserKnownHostsFile={known_hosts}", calls)
            self.assertIn("BatchMode=yes", calls)
            self.assertIn("PreferredAuthentications=publickey", calls)
            self.assertIn("PasswordAuthentication=no", calls)
            self.assertIn("openclaw@10.0.0.1", calls)
            self.assertNotIn("test-server-password", calls)
            self.assertNotIn("accept-new", calls)
            self.assertNotIn("plugins enable", calls)
            self.assertNotIn("gateway restart", calls)
            self.assertNotIn("agentswatch-backup", calls)
            self.assertIn("no backup or configuration change was made", result.stdout)

    def test_apply_config_script_has_no_password_or_accept_new_fallback(self):
        script = (ROOT / "scripts" / "apply_lobster_config.sh").read_text()

        self.assertNotIn("SSH_ASKPASS", script)
        self.assertNotIn("LOBSTER_SSH_PASSWORD", script)
        self.assertNotIn("StrictHostKeyChecking=accept-new", script)
        self.assertIn('StrictHostKeyChecking=yes', script)
        self.assertIn('PasswordAuthentication=no', script)
        self.assertIn('UserKnownHostsFile=${known_hosts_file}', script)

    def test_apply_config_success_keeps_mode_0600_timestamped_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            calls = root / "openclaw.log"
            config = root / "openclaw.json"
            original = '{"original":true}\n'
            config.write_text(original)
            config.chmod(0o640)
            fake_openclaw = fake_bin / "openclaw"
            fake_openclaw.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_OPENCLAW_LOG\"\n"
                "if [ \"${1:-} ${2:-}\" = 'config patch' ]; then\n"
                "  if [ \"${4:-}\" = --dry-run ]; then cat >/dev/null; else cat > \"$FAKE_CONFIG\"; fi\n"
                "fi\n"
            )
            fake_openclaw.chmod(0o700)
            patches = root / "patches"
            patches.mkdir()
            (patches / "lobster-001.json").write_text('{"patched":true}\n')
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_OPENCLAW_LOG"] = str(calls)
            env["FAKE_CONFIG"] = str(config)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "apply_lobster_config.sh"),
                    "lobster-001",
                    "--config-path",
                    str(config),
                    "--patch-dir",
                    str(patches),
                    "--health-timeout",
                    "2",
                    "--probe-timeout",
                    "1",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            backups = list(root.glob("openclaw.json.agentswatch-backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            self.assertEqual(config.read_text(), '{"patched":true}\n')
            self.assertIn(str(backups[0]), result.stdout)
            self.assertEqual(
                calls.read_text().splitlines(),
                [
                    "plugins inspect diagnostics-otel --json",
                    "plugins inspect diagnostics-prometheus --json",
                    "config patch --stdin --dry-run",
                    "plugins enable diagnostics-otel",
                    "plugins enable diagnostics-prometheus",
                    "config patch --stdin",
                    "gateway restart",
                    "gateway status",
                ],
            )

    def test_apply_config_health_failure_restores_backup_and_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            calls = root / "openclaw.log"
            config = root / "openclaw.json"
            original = '{"original":true}\n'
            config.write_text(original)
            fake_openclaw = fake_bin / "openclaw"
            fake_openclaw.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_OPENCLAW_LOG\"\n"
                "if [ \"${1:-} ${2:-}\" = 'config patch' ]; then\n"
                "  if [ \"${4:-}\" = --dry-run ]; then cat >/dev/null; else cat > \"$FAKE_CONFIG\"; fi\n"
                "fi\n"
                "if [ \"${1:-} ${2:-}\" = 'gateway status' ]; then exit 1; fi\n"
            )
            fake_openclaw.chmod(0o700)
            patches = root / "patches"
            patches.mkdir()
            (patches / "lobster-001.json").write_text('{"patched":true}\n')
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_OPENCLAW_LOG"] = str(calls)
            env["FAKE_CONFIG"] = str(config)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "apply_lobster_config.sh"),
                    "lobster-001",
                    "--config-path",
                    str(config),
                    "--patch-dir",
                    str(patches),
                    "--health-timeout",
                    "1",
                    "--health-interval",
                    "1",
                    "--probe-timeout",
                    "1",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            backups = list(root.glob("openclaw.json.agentswatch-backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            self.assertEqual(config.read_text(), original)
            call_lines = calls.read_text().splitlines()
            self.assertEqual(call_lines.count("gateway restart"), 2)
            self.assertIn("post-restart health check", result.stderr)
            self.assertIn("restored config backup", result.stderr)
            self.assertIn(str(backups[0]), result.stderr)

    def test_apply_config_container_dry_run_preserves_container_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            calls = root / "docker.log"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
                "case \"$*\" in *'config patch'*) cat >/dev/null ;; esac\n"
            )
            fake_docker.chmod(0o700)
            patches = root / "patches"
            patches.mkdir()
            (patches / "lobster-container.json").write_text("{}\n")
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_DOCKER_LOG"] = str(calls)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "apply_lobster_config.sh"),
                    "lobster-container",
                    "--container",
                    "openclaw",
                    "--patch-dir",
                    str(patches),
                    "--dry-run",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            call_lines = calls.read_text().splitlines()
            self.assertEqual(len(call_lines), 3)
            self.assertTrue(all(line.startswith("exec -i openclaw openclaw ") for line in call_lines))
            self.assertNotIn("restart", calls.read_text())
            self.assertNotIn("agentswatch-backup", calls.read_text())

    def test_apply_config_container_apply_backs_up_and_restarts_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_calls = root / "docker.log"
            openclaw_calls = root / "openclaw.log"
            config = root / "openclaw.json"
            config.write_text('{"original":true}\n')
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
                "if [ \"${1:-}\" = exec ]; then\n"
                "  shift\n"
                "  [ \"${1:-}\" = -i ] && shift\n"
                "  shift\n"
                "  exec \"$@\"\n"
                "fi\n"
                "[ \"${1:-}\" = restart ]\n"
            )
            fake_docker.chmod(0o700)
            fake_openclaw = fake_bin / "openclaw"
            fake_openclaw.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_OPENCLAW_LOG\"\n"
                "if [ \"${1:-} ${2:-}\" = 'config patch' ]; then\n"
                "  if [ \"${4:-}\" = --dry-run ]; then cat >/dev/null; else cat > \"$FAKE_CONFIG\"; fi\n"
                "fi\n"
            )
            fake_openclaw.chmod(0o700)
            patches = root / "patches"
            patches.mkdir()
            (patches / "lobster-container.json").write_text('{"patched":true}\n')
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_DOCKER_LOG"] = str(docker_calls)
            env["FAKE_OPENCLAW_LOG"] = str(openclaw_calls)
            env["FAKE_CONFIG"] = str(config)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "apply_lobster_config.sh"),
                    "lobster-container",
                    "--container",
                    "openclaw",
                    "--config-path",
                    str(config),
                    "--patch-dir",
                    str(patches),
                    "--health-timeout",
                    "2",
                    "--probe-timeout",
                    "1",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            backups = list(root.glob("openclaw.json.agentswatch-backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            self.assertEqual(backups[0].read_text(), '{"original":true}\n')
            self.assertEqual(config.read_text(), '{"patched":true}\n')
            self.assertIn("restart openclaw", docker_calls.read_text())
            self.assertIn("gateway status", openclaw_calls.read_text())

    def test_apply_config_loads_remote_bashrc_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            remote_home = root / "remote-home"
            remote_node_bin = remote_home / "custom-openclaw" / "bin"
            remote_node_bin.mkdir(parents=True)
            (remote_home / ".bashrc").write_text(
                'export PATH="$HOME/custom-openclaw/bin:$PATH"\n'
            )
            calls = root / "openclaw.log"

            fake_ssh = fake_bin / "ssh"
            fake_ssh.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    -o) shift 2 ;;\n"
                "    *) break ;;\n"
                "  esac\n"
                "done\n"
                "target=$1\n"
                "shift\n"
                "[ \"$target\" = openclaw@10.0.0.1 ]\n"
                "HOME=\"$FAKE_REMOTE_HOME\" PATH=/usr/bin:/bin sh -c \"$1\"\n"
            )
            fake_ssh.chmod(0o700)

            fake_node = remote_node_bin / "node"
            fake_node.write_text("#!/bin/sh\nexec /bin/sh \"$@\"\n")
            fake_node.chmod(0o700)
            fake_openclaw = remote_node_bin / "openclaw"
            fake_openclaw.write_text(
                "#!/usr/bin/env node\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$FAKE_OPENCLAW_CALLS\"\n"
                "if [ \"${1:-} ${2:-}\" = \"config patch\" ]; then cat >/dev/null; fi\n"
            )
            fake_openclaw.chmod(0o700)

            patches = root / "patches"
            patches.mkdir()
            (patches / "lobster-001.json").write_text("{}\n")
            known_hosts = root / "known_hosts"
            known_hosts.write_text("10.0.0.1 ssh-ed25519 AAAAC3Nza-test-only\n")
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_REMOTE_HOME"] = str(remote_home)
            env["FAKE_OPENCLAW_CALLS"] = str(calls)

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "apply_lobster_config.sh"),
                    "lobster-001",
                    "--ssh",
                    "openclaw@10.0.0.1",
                    "--known-hosts",
                    str(known_hosts),
                    "--patch-dir",
                    str(patches),
                    "--dry-run",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls.read_text().splitlines(),
                [
                    "plugins inspect diagnostics-otel --json",
                    "plugins inspect diagnostics-prometheus --json",
                    "config patch --stdin --dry-run",
                ],
            )


if __name__ == "__main__":
    unittest.main()
