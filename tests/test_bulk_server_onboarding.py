import base64
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

from agentswatch_api import bulk_server_onboarding as onboarding


SETTINGS = {
    "username": "openclaw",
    "port": 22,
    "credential_ref": "agentswatch-managed",
    "use_become": False,
}


def noop_preflight(plan, entries):
    return None


def host_key(host, suffix="planned"):
    data = base64.b64encode(f"{host}-{suffix}".encode()).decode()
    return f"{host} ssh-ed25519 {data}"


def scanner(suffix="planned"):
    def scan(host, port):
        return {"host": host, "port": port, "host_keys": [host_key(host, suffix)]}

    return scan


def server_row(number, internal_ip, **overrides):
    row = {
        "server_id": str(UUID(int=number)),
        "name": f"server-{number}",
        "internal_ip": internal_ip,
        "server_row_version": 1,
        "connection_row_version": None,
        "connection_type": None,
        "connection_host": None,
        "connection_port": None,
        "connection_username": None,
        "credential_ref": None,
        "use_become": False,
        "password_configured": False,
        "ssh_host_keys": [],
        "bootstrap_status": None,
        "bootstrap_completed_at": None,
        "connection_status": None,
    }
    row.update(overrides)
    return row


class ClassificationTests(unittest.TestCase):
    def test_classifies_missing_loopback_different_completed_and_actionable(self):
        missing = onboarding.classify_server(server_row(1, None), SETTINGS)
        loopback = onboarding.classify_server(server_row(2, "127.0.0.1"), SETTINGS)
        different = onboarding.classify_server(
            server_row(
                3,
                "10.0.0.3",
                connection_row_version=4,
                connection_type="ssh",
                connection_host="mgmt-03.example.internal",
            ),
            SETTINGS,
        )
        completed = onboarding.classify_server(
            server_row(
                4,
                "10.0.0.4",
                connection_row_version=5,
                connection_type="ssh",
                connection_host="10.0.0.4",
                connection_port=22,
                connection_username="openclaw",
                credential_ref="agentswatch-managed",
                password_configured=True,
                ssh_host_keys=[host_key("10.0.0.4")],
                bootstrap_status="succeeded",
                bootstrap_completed_at="2026-07-17T12:00:00Z",
                connection_status="reachable",
            ),
            SETTINGS,
        )
        actionable = onboarding.classify_server(
            server_row(
                5,
                "10.0.0.5",
                connection_row_version=2,
                connection_type="ssh",
                connection_host="127.0.0.1",
            ),
            SETTINGS,
        )

        self.assertEqual(missing["classification"], "missing_internal_ip")
        self.assertEqual(loopback["classification"], "loopback_internal_ip")
        self.assertEqual(
            different["classification"], "existing_different_management_address"
        )
        self.assertEqual(completed["classification"], "already_completed")
        self.assertEqual(actionable["classification"], "loopback_management_address")
        self.assertEqual(actionable["action"], "onboard")
        self.assertEqual(actionable["internal_ip"], "10.0.0.5")

        unsafe = onboarding.classify_server(
            server_row(
                7,
                "10.0.0.7",
                connection_row_version=2,
                connection_type="ssh",
                connection_host="127.1",
            ),
            SETTINGS,
        )
        self.assertEqual(unsafe["classification"], "unsafe_management_address")
        self.assertEqual(unsafe["action"], "onboard")

        different_port = onboarding.classify_server(
            server_row(
                6,
                "10.0.0.6",
                connection_row_version=3,
                connection_type="ssh",
                connection_host="10.0.0.6",
                connection_port=2222,
            ),
            SETTINGS,
        )
        self.assertEqual(
            different_port["classification"], "existing_different_management_address"
        )

    def test_default_settings_do_not_enable_become(self):
        plan = onboarding.build_plan(
            [server_row(1, "10.0.0.1")],
            username="openclaw",
            port=22,
            credential_ref="agentswatch-managed",
            scanner=scanner(),
        )

        self.assertFalse(plan["settings"]["use_become"])

        become_plan = onboarding.build_plan(
            [server_row(2, "10.0.0.2")],
            username="openclaw",
            port=22,
            credential_ref="agentswatch-managed",
            use_become=True,
            scanner=scanner(),
        )
        self.assertTrue(become_plan["settings"]["use_become"])


class PlanTests(unittest.TestCase):
    def test_explicit_server_subset_is_canonical_and_missing_or_inactive_fails_closed(self):
        rows = [server_row(1, "10.0.0.1"), server_row(2, "10.0.0.2")]

        selected = onboarding.select_requested_servers(
            rows, [UUID(int=2), str(UUID(int=2))]
        )
        self.assertEqual([row["server_id"] for row in selected], [str(UUID(int=2))])

        with self.assertRaisesRegex(
            onboarding.BulkOnboardingRejected, "missing, disabled, or retired"
        ):
            onboarding.select_requested_servers(rows, [UUID(int=3)])

        parsed = onboarding.parser().parse_args([
            "plan",
            "--output", "pilot.json",
            "--username", "openclaw",
            "--port", "22",
            "--credential-ref", "agentswatch-managed",
            "--server-id", str(UUID(int=2)),
            "--server-id", str(UUID(int=1)),
        ])
        self.assertEqual(parsed.server_ids, [UUID(int=2), UUID(int=1)])

    def test_plan_is_deterministic_secret_free_and_written_mode_0600(self):
        rows = [
            server_row(2, "10.0.0.2"),
            server_row(1, "10.0.0.1"),
            server_row(3, None),
        ]
        first = onboarding.build_plan(rows, **SETTINGS, scanner=scanner())
        second = onboarding.build_plan(reversed(rows), **SETTINGS, scanner=scanner())

        self.assertEqual(first, second)
        serialized = onboarding.canonical_json_bytes(first)
        self.assertNotIn(b"ssh_password", serialized)
        self.assertNotIn(b"encrypted_password", serialized)
        self.assertEqual(
            [entry["server_id"] for entry in first["servers"]],
            sorted(entry["server_id"] for entry in first["servers"]),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "onboarding-plan.json"
            digest = onboarding.atomic_write_plan(path, first)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(digest, onboarding.file_sha256(path))
            loaded, loaded_digest = onboarding.load_confirmed_plan(path, digest)
            self.assertEqual(loaded, first)
            self.assertEqual(loaded_digest, digest)

    def test_scan_uses_bounded_concurrency_and_scan_failure_is_skipped(self):
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        def bounded_scanner(host, port):
            with lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            try:
                time.sleep(0.02)
                if host == "10.0.0.4":
                    raise RuntimeError("scan failed")
                return {"host_keys": [host_key(host)]}
            finally:
                with lock:
                    state["active"] -= 1

        plan = onboarding.build_plan(
            [server_row(index, f"10.0.0.{index}") for index in range(1, 6)],
            **SETTINGS,
            scan_workers=2,
            scanner=bounded_scanner,
        )

        self.assertGreaterEqual(state["maximum"], 2)
        self.assertLessEqual(state["maximum"], 2)
        failed = next(
            entry for entry in plan["servers"] if entry["internal_ip"] == "10.0.0.4"
        )
        self.assertEqual(failed["action"], "skip")
        self.assertEqual(failed["reason"], "host_key_scan_failed")
        self.assertIsNone(failed["scan"])

    def test_duplicate_internal_ips_are_never_scanned_or_applied(self):
        scanned = []

        def recording_scanner(host, port):
            scanned.append(host)
            return {"host_keys": [host_key(host)]}

        plan = onboarding.build_plan(
            [
                server_row(1, "10.0.0.9"),
                server_row(
                    2,
                    "10.0.0.9",
                    connection_row_version=2,
                    connection_type="ssh",
                    connection_host="10.0.0.9",
                    connection_port=22,
                    connection_username="openclaw",
                    credential_ref="agentswatch-managed",
                    password_configured=True,
                    ssh_host_keys=[host_key("10.0.0.9")],
                    bootstrap_status="succeeded",
                    bootstrap_completed_at="2026-07-17T12:00:00Z",
                    connection_status="reachable",
                ),
            ],
            **SETTINGS,
            scanner=recording_scanner,
        )

        self.assertEqual(scanned, [])
        self.assertTrue(
            all(entry["classification"] == "duplicate_internal_ip" for entry in plan["servers"])
        )
        self.assertTrue(all(entry["action"] == "skip" for entry in plan["servers"]))

    def test_pilot_subset_still_detects_duplicate_ip_in_all_active_servers(self):
        active_rows = [server_row(1, "10.0.0.9"), server_row(2, "10.0.0.9")]
        pilot_rows = onboarding.select_requested_servers(active_rows, [UUID(int=1)])
        scanned = []

        plan = onboarding.build_plan(
            pilot_rows,
            **SETTINGS,
            duplicate_check_rows=active_rows,
            scanner=lambda host, port: scanned.append(host),
        )

        self.assertEqual(scanned, [])
        self.assertEqual(len(plan["servers"]), 1)
        self.assertEqual(plan["servers"][0]["classification"], "duplicate_internal_ip")
        self.assertEqual(plan["servers"][0]["action"], "skip")

    def test_load_rejects_wrong_hash_and_insecure_mode(self):
        plan = onboarding.build_plan(
            [server_row(1, "10.0.0.1")], **SETTINGS, scanner=scanner()
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            digest = onboarding.atomic_write_plan(path, plan)
            with self.assertRaisesRegex(onboarding.BulkOnboardingRejected, "SHA-256 mismatch"):
                onboarding.load_confirmed_plan(path, "f" * 64)
            path.chmod(0o640)
            with self.assertRaisesRegex(onboarding.BulkOnboardingRejected, "group or other"):
                onboarding.load_confirmed_plan(path, digest)


class PlatformPreflightTests(unittest.TestCase):
    def test_preflight_validates_published_catalog_and_active_runs(self):
        plan = onboarding.build_plan(
            [server_row(1, "10.0.0.1")], **SETTINGS, scanner=scanner()
        )
        operation = SimpleNamespace(
            scope="server",
            supports_batch=False,
            version_id="published-version-id",
            playbook=mock.Mock(is_file=mock.Mock(return_value=True)),
            source_paths=(),
        )
        catalog = mock.Mock()
        catalog.get.return_value = operation
        database = mock.Mock()
        database.fetch_all.return_value = []

        with (
            mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/tool"),
            mock.patch.object(onboarding.importlib.util, "find_spec", return_value=object()),
            mock.patch.object(onboarding, "ServerCredentialCipher"),
            mock.patch.object(
                onboarding.OperationService,
                "_credential_files",
                return_value={"agentswatch-managed": "/credentials/key"},
            ),
            mock.patch.object(onboarding, "public_key_from_private_file"),
            mock.patch.object(onboarding, "ManagedOperationCatalog", return_value=catalog),
            mock.patch.object(onboarding.os, "access", return_value=True),
        ):
            context = onboarding.verify_platform_preconditions(
                plan, plan["servers"], database=database,
            )

        self.assertIs(context.catalog, catalog)
        catalog.validate_parameters.assert_called_once_with("server.bootstrap_ssh", {})
        database.fetch_all.assert_called_once()

    def test_preflight_rejects_unpublished_catalog_before_active_run_check(self):
        plan = onboarding.build_plan(
            [server_row(1, "10.0.0.1")], **SETTINGS, scanner=scanner()
        )
        operation = SimpleNamespace(
            scope="server", supports_batch=False, version_id=None,
            playbook=mock.Mock(is_file=mock.Mock(return_value=True)), source_paths=(),
        )
        catalog = mock.Mock()
        catalog.get.return_value = operation
        database = mock.Mock()

        with (
            mock.patch.object(onboarding.shutil, "which", return_value="/usr/bin/tool"),
            mock.patch.object(onboarding.importlib.util, "find_spec", return_value=object()),
            mock.patch.object(onboarding, "ServerCredentialCipher"),
            mock.patch.object(
                onboarding.OperationService,
                "_credential_files",
                return_value={"agentswatch-managed": "/credentials/key"},
            ),
            mock.patch.object(onboarding, "public_key_from_private_file"),
            mock.patch.object(onboarding, "ManagedOperationCatalog", return_value=catalog),
            mock.patch.object(onboarding.os, "access", return_value=True),
            self.assertRaisesRegex(
                onboarding.BulkOnboardingRejected, "published catalog contract",
            ),
        ):
            onboarding.verify_platform_preconditions(
                plan, plan["servers"], database=database,
            )
        database.fetch_all.assert_not_called()


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            server_row(1, "10.0.0.1"),
            server_row(2, "10.0.0.2"),
            server_row(3, None),
        ]
        self.plan = onboarding.build_plan(
            self.rows, **SETTINGS, scanner=scanner("planned")
        )

    def test_apply_requires_acceptance_and_matching_fresh_scan_before_password(self):
        prompts = []
        services = []

        def password_reader(prompt):
            prompts.append(prompt)
            return "must-not-be-read"

        with self.assertRaisesRegex(
            onboarding.BulkOnboardingRejected, "accept-scanned-host-keys"
        ):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=self.rows,
                accept_scanned_host_keys=False,
                actor_ref="bulk-test",
                scanner=scanner("planned"),
                password_reader=password_reader,
                server_service_factory=lambda: services.append(object()),
            )
        self.assertEqual(prompts, [])
        self.assertEqual(services, [])

        with self.assertRaisesRegex(
            onboarding.BulkOnboardingRejected, "differs from the confirmed plan"
        ):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=self.rows,
                accept_scanned_host_keys=True,
                actor_ref="bulk-test",
                scanner=scanner("changed"),
                preflight_checker=noop_preflight,
                password_reader=password_reader,
                server_service_factory=lambda: services.append(object()),
            )
        self.assertEqual(prompts, [])
        self.assertEqual(services, [])

    def test_platform_preflight_failure_precedes_scan_password_and_write(self):
        calls = {"scan": 0, "password": 0, "service": 0}

        def reject_preflight(plan, entries):
            raise onboarding.BulkOnboardingRejected("preflight rejected")

        with self.assertRaisesRegex(onboarding.BulkOnboardingRejected, "preflight rejected"):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=self.rows,
                accept_scanned_host_keys=True,
                actor_ref="bulk-test",
                scanner=lambda host, port: calls.__setitem__("scan", calls["scan"] + 1),
                preflight_checker=reject_preflight,
                password_reader=lambda prompt: calls.__setitem__(
                    "password", calls["password"] + 1,
                ),
                server_service_factory=lambda: calls.__setitem__(
                    "service", calls["service"] + 1,
                ),
            )
        self.assertEqual(calls, {"scan": 0, "password": 0, "service": 0})

    def test_active_run_appearing_after_fresh_scan_precedes_password_and_write(self):
        calls = {"scan": 0, "password": 0, "service": 0}

        class Context:
            catalog = None
            checks = 0

            def assert_no_active_runs(self, entries):
                self.checks += 1
                if self.checks == 2:
                    raise onboarding.BulkOnboardingRejected("active run appeared")

        context = Context()

        def preflight(plan, entries):
            context.assert_no_active_runs(entries)
            return context

        def fresh_scanner(host, port):
            calls["scan"] += 1
            return scanner("planned")(host, port)

        with self.assertRaisesRegex(onboarding.BulkOnboardingRejected, "active run appeared"):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=self.rows,
                accept_scanned_host_keys=True,
                actor_ref="bulk-test",
                scanner=fresh_scanner,
                preflight_checker=preflight,
                state_reader=lambda: self.rows,
                password_reader=lambda prompt: calls.__setitem__(
                    "password", calls["password"] + 1,
                ),
                server_service_factory=lambda: calls.__setitem__(
                    "service", calls["service"] + 1,
                ),
                operation_service_factory=lambda: object(),
            )
        self.assertEqual(calls["scan"], 2)
        self.assertEqual(calls["password"], 0)
        self.assertEqual(calls["service"], 0)

    def test_password_confirmation_mismatch_precedes_write(self):
        passwords = iter(("first-value", "different-value"))
        services = []
        with self.assertRaisesRegex(
            onboarding.BulkOnboardingRejected, "confirmation does not match",
        ):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=self.rows,
                accept_scanned_host_keys=True,
                actor_ref="bulk-test",
                scanner=scanner("planned"),
                preflight_checker=noop_preflight,
                password_reader=lambda prompt: next(passwords),
                server_service_factory=lambda: services.append(object()),
            )
        self.assertEqual(services, [])

    def test_apply_confirms_password_uses_services_and_single_target_runs(self):
        state = {
            "prompts": 0,
            "connections": [],
            "runs": [],
            "active": 0,
            "maximum": 0,
        }
        lock = threading.Lock()
        password = "SharedPassword-Only-In-Memory"

        def password_reader(prompt):
            state["prompts"] += 1
            return password

        class FakeServerService:
            def save_connection(self, server_id, values, actor):
                with lock:
                    state["connections"].append(
                        {"server_id": server_id, "values": dict(values), "actor": actor}
                    )

        class FakeOperationService:
            def create_run(
                self, operation_code, server_ids=None, group_id=None, parameters=None,
                requested_by="local-user",
            ):
                self.assert_single(operation_code, server_ids, group_id, parameters)
                run_id = server_ids[0]
                with lock:
                    state["runs"].append(
                        {
                            "run_id": run_id,
                            "server_ids": list(server_ids),
                            "requested_by": requested_by,
                        }
                    )
                return run_id

            @staticmethod
            def assert_single(operation_code, server_ids, group_id, parameters):
                if operation_code != "server.bootstrap_ssh":
                    raise AssertionError(operation_code)
                if group_id is not None or len(server_ids or []) != 1 or parameters != {}:
                    raise AssertionError("bootstrap run was not single-target")

            def execute_run(self, run_id):
                with lock:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                try:
                    time.sleep(0.02)
                finally:
                    with lock:
                        state["active"] -= 1

        result = onboarding.apply_plan_document(
            self.plan,
            current_rows=self.rows,
            accept_scanned_host_keys=True,
            actor_ref="bulk-test",
            max_workers=2,
            scanner=scanner("planned"),
            preflight_checker=noop_preflight,
            password_reader=password_reader,
            server_service_factory=FakeServerService,
            operation_service_factory=FakeOperationService,
            result_reader=lambda run_id: {"status": "succeeded", "error": None},
        )

        self.assertEqual(state["prompts"], 2)
        self.assertEqual(len(state["connections"]), 2)
        self.assertEqual(len(state["runs"]), 2)
        self.assertGreaterEqual(state["maximum"], 2)
        self.assertLessEqual(state["maximum"], 2)
        for connection in state["connections"]:
            values = connection["values"]
            self.assertEqual(values["ssh_password"], password)
            self.assertEqual(values["username"], "openclaw")
            self.assertEqual(values["credential_ref"], "agentswatch-managed")
            self.assertFalse(values["use_become"])
            self.assertEqual(values["host"], f"10.0.0.{connection['server_id'].int}")
        self.assertTrue(all(len(run["server_ids"]) == 1 for run in state["runs"]))
        self.assertEqual(
            result["summary"],
            {"total": 3, "succeeded": 2, "failed": 0, "skipped": 1},
        )
        self.assertNotIn(password, json.dumps(result, ensure_ascii=False))

    def test_failure_output_redacts_the_prompted_password(self):
        password = "DoNotLeakThisPassword"

        class FailingService:
            def save_connection(self, server_id, values, actor):
                raise RuntimeError(f"password={values['ssh_password']}")

        result = onboarding.apply_plan_document(
            self.plan,
            current_rows=self.rows,
            accept_scanned_host_keys=True,
            actor_ref="bulk-test",
            scanner=scanner("planned"),
            preflight_checker=noop_preflight,
            password_reader=lambda prompt: password,
            server_service_factory=FailingService,
        )

        output = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(password, output)
        self.assertIn("[REDACTED]", output)
        self.assertEqual(result["summary"]["failed"], 2)

    def test_apply_rejects_stale_server_state_before_password_or_write(self):
        changed = [dict(row) for row in self.rows]
        changed[0]["server_row_version"] = 2
        called = {"password": 0, "service": 0}

        def password_reader(prompt):
            called["password"] += 1
            return "unused"

        def service_factory():
            called["service"] += 1
            raise AssertionError("must not construct a write service")

        with self.assertRaisesRegex(
            onboarding.BulkOnboardingRejected, "state changed since planning"
        ):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=changed,
                accept_scanned_host_keys=True,
                actor_ref="bulk-test",
                scanner=scanner("planned"),
                password_reader=password_reader,
                server_service_factory=service_factory,
            )
        self.assertEqual(called, {"password": 0, "service": 0})

    def test_apply_rejects_new_global_duplicate_before_scan_password_or_write(self):
        current_rows = [dict(row) for row in self.rows]
        current_rows.append(server_row(4, "10.0.0.1"))
        called = {"scan": 0, "password": 0, "service": 0}

        def fresh_scanner(host, port):
            called["scan"] += 1
            return scanner("planned")(host, port)

        def password_reader(prompt):
            called["password"] += 1
            return "unused"

        def service_factory():
            called["service"] += 1
            raise AssertionError("must not construct a write service")

        with self.assertRaisesRegex(
            onboarding.BulkOnboardingRejected, "duplicate_internal_ip"
        ):
            onboarding.apply_plan_document(
                self.plan,
                current_rows=current_rows,
                accept_scanned_host_keys=True,
                actor_ref="bulk-test",
                scanner=fresh_scanner,
                password_reader=password_reader,
                server_service_factory=service_factory,
            )
        self.assertEqual(called, {"scan": 0, "password": 0, "service": 0})


if __name__ == "__main__":
    unittest.main()
