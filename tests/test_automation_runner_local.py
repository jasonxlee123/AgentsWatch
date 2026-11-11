import fcntl
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agentswatch_automation import AutomationRunner, cleanup_stale_runner_workspaces
from agentswatch_automation.catalog import OperationDefinition
from agentswatch_automation.paths import AUTOMATION_DATA_ROOT_ENV
from agentswatch_automation.runner import RUNNER_WORKSPACE_LOCK, RUNNER_WORKSPACE_MARKER
from agentswatch_api.operation_events import _truncate_utf8


class AutomationEventSanitizationTests(unittest.TestCase):
    def test_event_snapshot_redacts_credentials_and_strips_unsafe_result_fields(self):
        snapshot = AutomationRunner._event_snapshot({
            "event": "runner_on_failed",
            "stdout": "\x1b[31mfailed password=plain-secret postgresql://user:db-secret@localhost/db\x1b[0m",
            "event_data": {
                "host": "server-1",
                "task": "safe task",
                "res": {
                    "changed": False,
                    "msg": "Bearer token-value-123456",
                    "invocation": {"module_args": {"password": "plain-secret"}},
                    "ansible_facts": {"private": "value"},
                },
            },
        })
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("plain-secret", serialized)
        self.assertNotIn("db-secret", serialized)
        self.assertNotIn("token-value-123456", serialized)
        self.assertNotIn("invocation", serialized)
        self.assertNotIn("module_args", serialized)
        self.assertNotIn("ansible_facts", serialized)
        self.assertNotIn("\x1b", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_no_log_hides_stdout_and_message(self):
        snapshot = AutomationRunner._event_snapshot({
            "event": "runner_on_ok",
            "stdout": "secret output",
            "event_data": {"host": "server-1", "res": {"_ansible_no_log": True, "msg": "secret message"}},
        })
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("secret output", serialized)
        self.assertNotIn("secret message", serialized)
        self.assertIn("no_log", serialized)

    def test_json_and_escaped_json_named_secrets_are_redacted(self):
        from agentswatch_automation.redaction import redact_sensitive_data, redact_sensitive_text

        plain = redact_sensitive_text('{"appSecret":"json-secret","token":"json-token"}')
        escaped = redact_sensitive_text(r'{\"appSecret\":\"escaped-secret\"}')
        structured = redact_sensitive_data({
            "appSecret": "structured-secret",
            "nested": {"api_key": "structured-api-key"},
        })
        serialized = json.dumps({
            "plain": plain,
            "escaped": escaped,
            "structured": structured,
        })
        for secret in (
            "json-secret", "json-token", "escaped-secret",
            "structured-secret", "structured-api-key",
        ):
            self.assertNotIn(secret, serialized)
        self.assertGreaterEqual(serialized.count("[REDACTED]"), 5)

    def test_utf8_truncation_respects_byte_limit(self):
        result = _truncate_utf8("汉" * 10000)
        self.assertLessEqual(len(result.encode("utf-8")), 16000)
        self.assertTrue(result.endswith("输出已截断"))


class RunnerWorkspaceSecurityTests(unittest.TestCase):
    def test_cleanup_removes_only_unlocked_marked_runner_workspaces(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-cleanup-") as directory:
            data_root = Path(directory)
            runner = data_root / ".runner"
            runner.mkdir()
            unmarked = runner / "user-owned"
            unmarked.mkdir()
            artifacts = data_root / "artifacts" / "existing-run"
            artifacts.mkdir(parents=True)
            (artifacts / "stdout").write_text("keep", encoding="utf-8")

            stale = runner / "stale"
            stale.mkdir()
            (stale / RUNNER_WORKSPACE_MARKER).write_text("pid=1\n", encoding="utf-8")
            (stale / RUNNER_WORKSPACE_LOCK).write_text("", encoding="utf-8")

            live = runner / "live"
            live.mkdir()
            (live / RUNNER_WORKSPACE_MARKER).write_text("pid=2\n", encoding="utf-8")
            live_lock = live / RUNNER_WORKSPACE_LOCK
            live_lock.write_text("", encoding="utf-8")
            live_fd = os.open(live_lock, os.O_RDWR)
            fcntl.flock(live_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: str(data_root)}):
                    self.assertEqual(cleanup_stale_runner_workspaces(), 1)
                    self.assertFalse(stale.exists())
                    self.assertTrue(live.exists())
                    self.assertTrue(unmarked.exists())
                    self.assertEqual((artifacts / "stdout").read_text(encoding="utf-8"), "keep")
                    self.assertEqual(runner.stat().st_mode & 0o777, 0o700)
            finally:
                fcntl.flock(live_fd, fcntl.LOCK_UN)
                os.close(live_fd)
            with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: str(data_root)}):
                self.assertEqual(cleanup_stale_runner_workspaces(), 1)
            self.assertFalse(live.exists())


@unittest.skipUnless(importlib.util.find_spec("ansible_runner"), "ansible-runner is not installed")
class AutomationRunnerLocalTests(unittest.TestCase):
    target = {
        "server_id": "local-test-server",
        "address": "127.0.0.1",
        "connection": "local",
        "python_interpreter": "/usr/bin/python3",
    }

    def test_external_managed_playbook_and_mutable_data_root(self):
        class StaticCatalog:
            def __init__(self, operation):
                self.operation = operation

            def get(self, code):
                if code != self.operation.code:
                    raise KeyError(code)
                return self.operation

            def validate_parameters(self, code, parameters):
                self.get(code)
                return dict(parameters or {})

        with tempfile.TemporaryDirectory(prefix="agentswatch-external-runner-") as directory:
            data_root = Path(directory).resolve()
            playbook = data_root / "managed" / "external.yml"
            playbook.parent.mkdir()
            playbook.write_text(
                """---
- name: Execute an externally stored managed playbook
  hosts: all
  gather_facts: false
  tasks:
    - name: Return a structured result
      ansible.builtin.set_fact:
        agentswatch_result:
          external_managed_playbook: true
          nested:
            appSecret: runner-structured-secret
            password: runner-structured-password
""",
                encoding="utf-8",
            )
            operation = OperationDefinition(
                code="server.external_managed_test",
                name="外置 Playbook 测试",
                description="验证外置可变数据目录",
                category="测试",
                scope="server",
                version="1.0.0",
                playbook=playbook,
                source_paths=(),
                risk_level="low",
                requires_become=False,
                supports_batch=True,
                timeout_seconds=30,
                parameters={},
            )
            with patch.dict(os.environ, {AUTOMATION_DATA_ROOT_ENV: str(data_root)}):
                report = AutomationRunner(StaticCatalog(operation)).run(
                    operation.code,
                    [self.target],
                    run_id="external-managed-storage",
                )

            self.assertEqual(report["return_code"], 0)
            self.assertTrue(
                report["hosts"]["local-test-server"]["result"]["external_managed_playbook"],
            )
            nested = report["hosts"]["local-test-server"]["result"]["nested"]
            self.assertEqual(nested["appSecret"], "[REDACTED]")
            self.assertEqual(nested["password"], "[REDACTED]")
            self.assertNotIn(
                "runner-structured-secret",
                json.dumps(report, ensure_ascii=False),
            )
            self.assertEqual(
                Path(report["artifact_dir"]),
                data_root / "artifacts" / "external-managed-storage",
            )
            self.assertEqual(
                {path.name for path in Path(report["artifact_dir"]).iterdir()},
                {"stdout"},
            )
            self.assertEqual(list((data_root / ".runner").iterdir()), [])

    def test_runner_process_and_private_artifacts_do_not_receive_api_secrets(self):
        class StaticCatalog:
            def __init__(self, operation):
                self.operation = operation

            def get(self, code):
                if code != self.operation.code:
                    raise KeyError(code)
                return self.operation

            def validate_parameters(self, code, parameters):
                self.get(code)
                return dict(parameters or {})

        marker = "agentswatch-secret-marker-7f988da9"
        with tempfile.TemporaryDirectory(prefix="agentswatch-env-isolation-") as directory:
            data_root = Path(directory).resolve()
            playbook = data_root / "managed" / "environment.yml"
            playbook.parent.mkdir()
            playbook.write_text(
                """---
- name: Verify the child environment is isolated
  hosts: all
  gather_facts: false
  tasks:
    - name: Return whether API secrets are absent
      ansible.builtin.set_fact:
        agentswatch_result:
          database_url_absent: "{{ lookup('ansible.builtin.env', 'AGENTSWATCH_DATABASE_URL') | length == 0 }}"
          fernet_key_absent: "{{ lookup('ansible.builtin.env', 'AGENTSWATCH_WEB_CREDENTIAL_KEY') | length == 0 }}"
""",
                encoding="utf-8",
            )
            operation = OperationDefinition(
                code="server.environment_isolation_test",
                name="环境隔离测试",
                description="验证执行器不会继承 API 密钥",
                category="测试",
                scope="server",
                version="1.0.0",
                playbook=playbook,
                source_paths=(),
                risk_level="low",
                requires_become=False,
                supports_batch=True,
                timeout_seconds=30,
                parameters={},
            )
            real_rmtree = shutil.rmtree

            def preserve_runner_workspace(path, *args, **kwargs):
                candidate = Path(path)
                if candidate.parent == data_root / ".runner":
                    return None
                return real_rmtree(path, *args, **kwargs)

            environment = {
                AUTOMATION_DATA_ROOT_ENV: str(data_root),
                "AGENTSWATCH_DATABASE_URL": f"postgresql://user:{marker}@localhost/db",
                "AGENTSWATCH_WRITE_DATABASE_URL": f"postgresql://writer:{marker}@localhost/db",
                "AGENTSWATCH_WEB_CREDENTIAL_KEY": marker,
            }
            try:
                with patch.dict(os.environ, environment), patch(
                    "agentswatch_automation.runner.shutil.rmtree",
                    side_effect=preserve_runner_workspace,
                ):
                    report = AutomationRunner(StaticCatalog(operation)).run(
                        operation.code, [self.target], run_id="environment-isolation",
                    )
                result = report["hosts"]["local-test-server"]["result"]
                self.assertTrue(result["database_url_absent"])
                self.assertTrue(result["fernet_key_absent"])
                private_files = [path for path in (data_root / ".runner").rglob("*") if path.is_file()]
                self.assertTrue(any(path.name == "command" for path in private_files))
                for path in private_files + [path for path in (data_root / "artifacts").rglob("*") if path.is_file()]:
                    self.assertNotIn(marker, path.read_text(encoding="utf-8", errors="replace"), str(path))
            finally:
                real_rmtree(data_root / ".runner", ignore_errors=True)

    def test_connection_check(self):
        events = []
        report = AutomationRunner().run(
            "server.check_connection", [self.target], event_callback=events.append,
        )
        host = report["hosts"]["local-test-server"]
        self.assertEqual(report["return_code"], 0)
        self.assertEqual(host["status"], "succeeded")
        self.assertTrue(host["result"]["connected"])
        event_types = {event["event_type"] for event in events}
        self.assertIn("playbook_on_start", event_types)
        self.assertIn("playbook_on_task_start", event_types)
        self.assertIn("runner_on_ok", event_types)
        self.assertIn("playbook_on_stats", event_types)
        self.assertTrue(any(event["target_key"] == "local-test-server" for event in events))
        artifact_files = {path.name for path in Path(report["artifact_dir"]).iterdir()}
        self.assertEqual(artifact_files, {"stdout"})
        self.assertEqual((Path(report["artifact_dir"]) / "stdout").stat().st_mode & 0o777, 0o600)

    def test_fact_collection(self):
        report = AutomationRunner().run("server.collect_facts", [self.target])
        facts = report["hosts"]["local-test-server"]["result"]
        self.assertEqual(report["return_code"], 0)
        self.assertTrue(facts["hostname"])
        self.assertTrue(facts["operating_system"])
        self.assertGreater(int(facts["memory_mib"]), 0)
        self.assertIn("disk_gib", facts)

    def test_runtime_discovery_accepts_custom_safe_paths(self):
        report = AutomationRunner().run(
            "server.discover_runtimes",
            [self.target],
            {"runtime_search_paths": ["/tmp", "/path/that/does/not/exist"]},
        )
        discovery = report["hosts"]["local-test-server"]["result"]
        self.assertEqual(report["return_code"], 0)
        self.assertIn("/tmp", discovery["paths"])

    def test_unreachable_server_returns_per_host_failure(self):
        report = AutomationRunner().run(
            "server.check_connection",
            [{
                "server_id": "unreachable-test-server",
                "address": "127.0.0.1",
                "connection": "ssh",
                "port": 1,
                "user": "nobody",
            }],
        )
        host = report["hosts"]["unreachable-test-server"]
        self.assertNotEqual(report["return_code"], 0)
        self.assertEqual(host["status"], "unreachable")
        self.assertTrue(host["error"])

    def test_runtime_mock_driver_supports_standard_lifecycle_operations(self):
        target = {
            **self.target,
            "server_id": "local-test-runtime",
            "runtime_management_driver": "mock",
            "runtime_management_target": "openclaw-local-test",
        }
        for operation, expected_status in (
            ("runtime.refresh_status", "running"),
            ("runtime.start", "running"),
            ("runtime.stop", "stopped"),
            ("runtime.restart", "running"),
        ):
            report = AutomationRunner().run(operation, [target])
            self.assertEqual(report["return_code"], 0)
            self.assertEqual(report["hosts"]["local-test-runtime"]["result"]["observed_status"], expected_status)

    def test_runtime_information_collection_returns_safe_structured_observation(self):
        target = {
            **self.target,
            "server_id": "local-runtime-collection",
            "runtime_management_driver": "mock",
            "runtime_family_code": "openclaw",
            "runtime_config_path": "/tmp",
            "runtime_state_path": "/path/that/does/not/exist",
        }
        report = AutomationRunner().run("runtime.collect_info", [target])
        result = report["hosts"]["local-runtime-collection"]["result"]
        self.assertEqual(report["return_code"], 0)
        self.assertEqual(result["runtime_family_code"], "openclaw")
        self.assertEqual(result["actual_version"], "1.0.0")
        self.assertTrue(result["config_exists"])
        self.assertFalse(result["state_exists"])
        self.assertIsNone(result["observed_agent_count"])

    @unittest.skipUnless(shutil.which("openclaw"), "OpenClaw CLI is not installed")
    def test_openclaw_agent_deployment_is_isolated_and_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-agent-") as directory:
            root = Path(directory)
            primary_id, child_id = uuid4(), uuid4()
            primary_account_id = f"aw-{str(primary_id).replace('-', '')}"
            channel_secret = "isolated-feishu-secret"
            channel_patch = root / "feishu-patch.json"
            channel_patch.write_text(json.dumps({
                "channels": {
                    "feishu": {
                        "enabled": True,
                        "connectionMode": "websocket",
                        "accounts": {
                            primary_account_id: {
                                "enabled": True,
                                "appId": "cli_isolated_test",
                                "appSecret": channel_secret,
                                "name": "本机测试主智能体",
                            },
                        },
                    },
                },
            }), encoding="utf-8")
            channel_patch.chmod(0o600)
            target = {
                **self.target,
                "server_id": str(primary_id),
                "runtime_family_code": "openclaw",
                "runtime_config_path": str(root / "openclaw.json"),
                "runtime_state_path": str(root),
                "runtime_management_driver": "mock",
                "runtime_management_target": "agentswatch-test-runtime",
                "agentswatch_retired_agent_definitions": [],
                "agentswatch_channel_patch_file": str(channel_patch),
                "agentswatch_agent_definitions": [
                    {
                        "agent_id": str(primary_id),
                        "name": "本机测试主智能体",
                        "structure_role": "primary",
                        "runtime_agent_key": "agentswatch-test-main",
                        "workspace_path": str(root / "workspaces" / "agentswatch-test-main"),
                        "model": "",
                        "channel_type": "feishu",
                        "channel_account_id": primary_account_id,
                    },
                    {
                        "agent_id": str(child_id),
                        "name": "本机测试子智能体",
                        "structure_role": "subagent",
                        "runtime_agent_key": "agentswatch-test-child",
                        "workspace_path": str(root / "workspaces" / "agentswatch-test-child"),
                        "model": "",
                        "channel_type": "none",
                        "channel_account_id": f"aw-{str(child_id).replace('-', '')}",
                    },
                ],
            }
            parameters = {
                "runtime_id": str(uuid4()),
                "runtime_agent_key": "agentswatch-test-main",
            }
            first = AutomationRunner().run("agent.apply_deployment", [target], parameters)
            first_result = first["hosts"][str(primary_id)]["result"]
            self.assertEqual(first["return_code"], 0, first)
            self.assertEqual(first_result["created_agent_count"], 2)
            self.assertEqual(
                first_result["deployed_agent_keys"],
                ["agentswatch-test-main", "agentswatch-test-child"],
            )
            self.assertEqual(first_result["configured_channel_account_ids"], [primary_account_id])
            config = json.loads((root / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(
                config["channels"]["feishu"]["accounts"][primary_account_id]["appSecret"],
                channel_secret,
            )
            self.assertEqual(config["bindings"][0]["agentId"], "agentswatch-test-main")
            self.assertNotIn(channel_secret, str(first))
            self.assertNotIn(
                channel_secret,
                (Path(first["artifact_dir"]) / "stdout").read_text(encoding="utf-8"),
            )

            second = AutomationRunner().run("agent.apply_deployment", [target], parameters)
            second_result = second["hosts"][str(primary_id)]["result"]
            self.assertEqual(second["return_code"], 0)
            self.assertEqual(second_result["created_agent_count"], 0)

            ownership_path = root / ".agentswatch" / "agent-ownership.json"
            ownership_before_gate_checks = ownership_path.read_bytes()
            config_before_gate_checks = (root / "openclaw.json").read_bytes()
            cleanup_definitions = target["agentswatch_agent_definitions"]
            owned_keys = [item["runtime_agent_key"] for item in cleanup_definitions]
            try:
                ownership_path.write_text("{}\n", encoding="utf-8")
                blocked_restore = AutomationRunner().run(
                    "agent.apply_deployment",
                    [{
                        **target,
                        "agentswatch_allow_existing_agent_keys": owned_keys,
                        "agentswatch_require_owned_agent_keys": owned_keys,
                    }],
                    parameters,
                )
                self.assertNotEqual(blocked_restore["return_code"], 0)
                self.assertEqual(
                    (root / "openclaw.json").read_bytes(), config_before_gate_checks,
                )
                self.assertEqual(
                    json.loads(ownership_path.read_text(encoding="utf-8")), {},
                )

                blocked_source_cleanup = AutomationRunner().run(
                    "agent.remove_deployment",
                    [{
                        **target,
                        "agentswatch_agent_definitions": [],
                        "agentswatch_retired_agent_definitions": cleanup_definitions,
                        "agentswatch_ownership_cleanup_mode": "required",
                    }],
                    {"expected_deployment_id": str(uuid4())},
                )
                self.assertNotEqual(blocked_source_cleanup["return_code"], 0)
                self.assertEqual(
                    (root / "openclaw.json").read_bytes(), config_before_gate_checks,
                )

                safe_target_noop = AutomationRunner().run(
                    "agent.remove_deployment",
                    [{
                        **target,
                        "agentswatch_agent_definitions": [],
                        "agentswatch_retired_agent_definitions": cleanup_definitions,
                        "agentswatch_ownership_cleanup_mode": "compensation",
                    }],
                    {"expected_deployment_id": str(uuid4())},
                )
                safe_target_result = safe_target_noop["hosts"][str(primary_id)]["result"]
                self.assertEqual(safe_target_noop["return_code"], 0, safe_target_noop)
                self.assertTrue(safe_target_result["cleanup_not_required"])
                self.assertTrue(safe_target_result["skipped_no_ownership"])
                self.assertEqual(
                    (root / "openclaw.json").read_bytes(), config_before_gate_checks,
                )

                partial_ownership = json.loads(
                    ownership_before_gate_checks.decode("utf-8"),
                )
                partial_ownership.pop(owned_keys[-1])
                ownership_path.write_text(
                    json.dumps(partial_ownership), encoding="utf-8",
                )
                blocked_retired_cleanup = AutomationRunner().run(
                    "agent.apply_deployment",
                    [{
                        **target,
                        "agentswatch_agent_definitions": [cleanup_definitions[0]],
                        "agentswatch_retired_agent_definitions": [cleanup_definitions[1]],
                    }],
                    parameters,
                )
                self.assertNotEqual(blocked_retired_cleanup["return_code"], 0)
                self.assertEqual(
                    (root / "openclaw.json").read_bytes(), config_before_gate_checks,
                )
                blocked_partial_compensation = AutomationRunner().run(
                    "agent.remove_deployment",
                    [{
                        **target,
                        "agentswatch_agent_definitions": [],
                        "agentswatch_retired_agent_definitions": cleanup_definitions,
                        "agentswatch_ownership_cleanup_mode": "compensation",
                    }],
                    {"expected_deployment_id": str(uuid4())},
                )
                self.assertNotEqual(
                    blocked_partial_compensation["return_code"], 0,
                )
                self.assertEqual(
                    (root / "openclaw.json").read_bytes(), config_before_gate_checks,
                )
            finally:
                ownership_path.write_bytes(ownership_before_gate_checks)

            config_before_mutation_failure = (root / "openclaw.json").read_bytes()
            ownership_before_mutation_failure = json.loads(
                ownership_path.read_text(encoding="utf-8"),
            )
            failed_primary_id = uuid4()
            failed_key = "agentswatch-failed-reservation"
            failed_workspace = root / "workspaces" / failed_key
            malformed_secret = "malformed-channel-secret-marker"
            malformed_patch = root / "malformed-feishu-patch.json"
            malformed_patch.write_text(
                '{"appSecret":"' + malformed_secret + '"', encoding="utf-8",
            )
            malformed_patch.chmod(0o600)
            failed_definition = {
                "agent_id": str(failed_primary_id),
                "name": "事务失败后保留所有权",
                "structure_role": "primary",
                "runtime_agent_key": failed_key,
                "workspace_path": str(failed_workspace),
                "model": "",
                "channel_type": "feishu",
                "channel_account_id": f"aw-{str(failed_primary_id).replace('-', '')}",
            }
            mutation_failure_events = []
            mutation_failure = AutomationRunner().run(
                "agent.apply_deployment",
                [{
                    **target,
                    "server_id": str(failed_primary_id),
                    "agentswatch_agent_definitions": [failed_definition],
                    "agentswatch_retired_agent_definitions": [],
                    "agentswatch_channel_patch_file": str(malformed_patch),
                }],
                {
                    "runtime_id": str(uuid4()),
                    "runtime_agent_key": failed_key,
                },
                event_callback=mutation_failure_events.append,
            )
            self.assertNotEqual(mutation_failure["return_code"], 0)
            self.assertEqual(
                (root / "openclaw.json").read_bytes(),
                config_before_mutation_failure,
            )
            ownership_after_mutation_failure = json.loads(
                ownership_path.read_text(encoding="utf-8"),
            )
            self.assertTrue(all(
                ownership_after_mutation_failure.get(key) == value
                for key, value in ownership_before_mutation_failure.items()
            ))
            self.assertEqual(
                ownership_after_mutation_failure[failed_key],
                {
                    "primary_agent_id": str(failed_primary_id),
                    "workspace": str(failed_workspace),
                },
            )
            self.assertNotIn(malformed_secret, json.dumps(mutation_failure))
            self.assertNotIn(
                malformed_secret,
                (Path(mutation_failure["artifact_dir"]) / "stdout").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertNotIn(
                malformed_secret,
                json.dumps(mutation_failure_events, ensure_ascii=False),
            )

            foreign_primary_id = uuid4()
            foreign_reservation = AutomationRunner().run(
                "agent.apply_deployment",
                [{
                    **target,
                    "server_id": str(foreign_primary_id),
                    "agentswatch_agent_definitions": [{
                        **failed_definition,
                        "agent_id": str(foreign_primary_id),
                        "name": "不得接管残留 workspace",
                        "channel_type": "none",
                        "channel_account_id": f"aw-{str(foreign_primary_id).replace('-', '')}",
                    }],
                    "agentswatch_retired_agent_definitions": [],
                }],
                {
                    "runtime_id": str(uuid4()),
                    "runtime_agent_key": failed_key,
                },
            )
            self.assertNotEqual(foreign_reservation["return_code"], 0)
            self.assertEqual(
                ownership_after_mutation_failure,
                json.loads(ownership_path.read_text(encoding="utf-8")),
            )

            config_before_failed_adoption = (root / "openclaw.json").read_bytes()
            failed_adoption_events = []
            unknown_definition = {
                **target["agentswatch_agent_definitions"][0],
                "runtime_agent_key": "agentswatch-missing-imported-key",
                "workspace_path": str(root / "workspaces" / "agentswatch-missing-imported-key"),
            }
            failed_adoption = AutomationRunner().run(
                "agent.apply_deployment",
                [{
                    **target,
                    "agentswatch_agent_definitions": [unknown_definition],
                    "agentswatch_require_existing_agent_keys": [
                        "agentswatch-missing-imported-key",
                    ],
                }],
                {
                    "runtime_id": str(uuid4()),
                    "runtime_agent_key": "agentswatch-missing-imported-key",
                },
                event_callback=failed_adoption_events.append,
            )
            self.assertNotEqual(failed_adoption["return_code"], 0)
            self.assertEqual(
                (root / "openclaw.json").read_bytes(), config_before_failed_adoption,
            )
            failed_task_names = {
                event["task_name"] for event in failed_adoption_events
                if event.get("task_name")
            }
            self.assertNotIn("Create missing OpenClaw agents", failed_task_names)
            self.assertFalse(any("Restart" in name for name in failed_task_names))

            collision_events = []
            ownership_before_collision = ownership_path.read_bytes()
            foreign_ownership = json.loads(
                ownership_before_collision.decode("utf-8"),
            )
            foreign_ownership["agentswatch-test-main"]["primary_agent_id"] = str(uuid4())
            ownership_path.write_text(
                json.dumps(foreign_ownership), encoding="utf-8",
            )
            try:
                collision = AutomationRunner().run(
                    "agent.apply_deployment",
                    [{
                        **target,
                        "agentswatch_agent_definitions": [
                            target["agentswatch_agent_definitions"][0],
                        ],
                        "agentswatch_require_existing_agent_keys": [],
                    }],
                    parameters,
                    event_callback=collision_events.append,
                )
            finally:
                ownership_path.write_bytes(ownership_before_collision)
            self.assertNotEqual(collision["return_code"], 0)
            self.assertEqual(
                (root / "openclaw.json").read_bytes(), config_before_failed_adoption,
            )
            collision_task_names = {
                event["task_name"] for event in collision_events
                if event.get("task_name")
            }
            self.assertNotIn("Create missing OpenClaw agents", collision_task_names)
            self.assertFalse(any("Restart" in name for name in collision_task_names))

            new_child_id = uuid4()
            new_child_definition = {
                "agent_id": str(new_child_id),
                "name": "收编时新增子智能体",
                "structure_role": "subagent",
                "runtime_agent_key": "agentswatch-adoption-new-child",
                "workspace_path": str(root / "workspaces" / "agentswatch-adoption-new-child"),
                "model": "",
                "channel_type": "none",
                "channel_account_id": f"aw-{str(new_child_id).replace('-', '')}",
            }
            adoption_definitions = [
                {
                    **target["agentswatch_agent_definitions"][0],
                    "workspace_path": str(root / "guessed-import-workspace"),
                },
                target["agentswatch_agent_definitions"][1],
                new_child_definition,
            ]
            adopted_with_new_child = AutomationRunner().run(
                "agent.apply_deployment",
                [{
                    **target,
                    "agentswatch_agent_definitions": adoption_definitions,
                    "agentswatch_require_existing_agent_keys": ["agentswatch-test-main"],
                }],
                parameters,
            )
            adopted_result = adopted_with_new_child["hosts"][str(primary_id)]["result"]
            self.assertEqual(adopted_with_new_child["return_code"], 0, adopted_with_new_child)
            self.assertEqual(adopted_result["created_agent_count"], 1)
            self.assertIn(
                "agentswatch-adoption-new-child", adopted_result["deployed_agent_keys"],
            )
            self.assertEqual(
                adopted_result["agent_workspaces"]["agentswatch-test-main"],
                str(root / "workspaces" / "agentswatch-test-main"),
            )

            removed = AutomationRunner().run(
                "agent.remove_deployment",
                [{
                    **target,
                    "agentswatch_agent_definitions": [],
                    "agentswatch_retired_agent_definitions": adoption_definitions,
                }],
                {"expected_deployment_id": str(uuid4())},
            )
            removed_result = removed["hosts"][str(primary_id)]["result"]
            self.assertEqual(removed["return_code"], 0, removed)
            self.assertNotIn(channel_secret, json.dumps(removed))
            self.assertNotIn(
                channel_secret,
                (Path(removed["artifact_dir"]) / "stdout").read_text(encoding="utf-8"),
            )
            self.assertFalse(removed_result["agents_deleted"])
            self.assertFalse(removed_result["workspaces_deleted"])
            config = json.loads((root / "openclaw.json").read_text(encoding="utf-8"))
            self.assertNotIn(primary_account_id, config["channels"]["feishu"].get("accounts", {}))
            self.assertEqual(config.get("bindings", []), [])
            ownership = json.loads(
                (root / ".agentswatch" / "agent-ownership.json").read_text(encoding="utf-8"),
            )
            self.assertTrue({
                item["runtime_agent_key"] for item in adoption_definitions
            }.issubset(ownership))
            self.assertTrue(all(
                ownership[item["runtime_agent_key"]]["primary_agent_id"] == str(primary_id)
                for item in adoption_definitions
            ))
            agent_ids = {
                item["id"]
                for item in json.loads(subprocess.check_output(
                    ["openclaw", "agents", "list", "--json"],
                    text=True,
                    env={
                        **os.environ,
                        "OPENCLAW_CONFIG_PATH": str(root / "openclaw.json"),
                        "OPENCLAW_STATE_DIR": str(root),
                    },
                ))
            }
            self.assertTrue({"agentswatch-test-main", "agentswatch-test-child"}.issubset(agent_ids))

            relink_definitions = [
                {
                    **item,
                    "workspace_path": str(
                        root / "new-requested-workspaces" / item["runtime_agent_key"]
                    ),
                }
                for item in adoption_definitions
            ]
            relinked = AutomationRunner().run(
                "agent.apply_deployment",
                [{
                    **target,
                    "agentswatch_agent_definitions": relink_definitions,
                    "agentswatch_retired_agent_definitions": [],
                }],
                parameters,
            )
            relinked_result = relinked["hosts"][str(primary_id)]["result"]
            self.assertEqual(relinked["return_code"], 0, relinked)
            self.assertEqual(relinked_result["created_agent_count"], 0)
            self.assertEqual(
                relinked_result["agent_workspaces"]["agentswatch-test-main"],
                str(root / "workspaces" / "agentswatch-test-main"),
            )

    def test_runtime_launchd_driver_reads_local_openclaw_status_when_available(self):
        if platform.system() != "Darwin":
            self.skipTest("launchd is only available on macOS")
        uid = subprocess.check_output(["/usr/bin/id", "-u"], text=True).strip()
        available = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{uid}/ai.openclaw.gateway"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if available.returncode != 0:
            self.skipTest("local OpenClaw LaunchAgent is not loaded")
        target = {
            **self.target,
            "server_id": "local-openclaw-launchd",
            "runtime_management_driver": "launchd",
            "runtime_management_target": "ai.openclaw.gateway",
            "runtime_endpoint_port": 16402,
            "runtime_probe_host": "127.0.0.1",
        }
        report = AutomationRunner().run("runtime.refresh_status", [target])
        self.assertEqual(report["return_code"], 0)
        self.assertEqual(report["hosts"]["local-openclaw-launchd"]["result"]["observed_status"], "running")
        self.assertTrue(report["hosts"]["local-openclaw-launchd"]["result"]["endpoint_reachable"])


if __name__ == "__main__":
    unittest.main()
