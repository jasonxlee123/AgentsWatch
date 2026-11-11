import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from agentswatch_observability.api import create_app
from agentswatch_observability.config import ControlConfig
from agentswatch_observability.control import (
    InventoryController,
    SnapshotConflict,
    reconcile_snapshot,
)


def active_row(runtime_id="runtime-1", gateway_id="gateway-1", name="龙虾一"):
    return {
        "runtime_id": runtime_id,
        "gateway_id": gateway_id,
        "server_id": f"server-{runtime_id}",
        "server_display_id": f"ECS-{runtime_id}",
        "primary_agent_id": f"agent-{runtime_id}",
        "agent_deployment_id": f"deployment-{runtime_id}",
        "lobster_name": name,
        "display_name": f"{name}-ECS-{runtime_id}",
        "host": "10.0.0.11",
        "port": 16402,
        "enabled": True,
        "deployment_mode": "baremetal",
        "env": "prod",
        "region": "cn-beijing",
        "team": "ops",
    }


class ObservabilityControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inventory = self.root / "lobsters.json"
        self.overlay = self.root / "overlay.json"
        self.state = self.root / "state"
        self.write_inventory(
            [
                active_row(),
                {
                    **active_row("runtime-2", "gateway-2", "龙虾二"),
                    "gateway_id": "",
                    "host": "",
                },
            ]
        )
        self.config = ControlConfig(
            state_root=self.state,
            local_inventory_path=self.inventory,
            overlay_path=self.overlay,
            database_url="",
            otel_endpoint="http://otel.example:4318",
            prometheus_reload_url="",
        )
        self.controller = InventoryController(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def write_inventory(self, gateways):
        self.inventory.write_text(
            json.dumps(
                {
                    "defaults": {"otel_endpoint": "http://otel.example:4318"},
                    "gateways": gateways,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_sync_retains_blocked_rows_and_activates_validated_revision(self):
        preview = self.controller.preview("local-json")
        self.assertEqual(preview["summary"]["total"], 2)
        self.assertEqual(preview["summary"]["deployable"], 1)
        self.assertTrue(preview["activation_allowed"])
        self.assertEqual(preview["activation_blockers"], [])
        self.assertEqual(
            [item["action"] for item in preview["actions"]],
            ["add", "retain_blocked"],
        )

        result = self.controller.sync(
            "local-json", expected_snapshot_hash=preview["snapshot_hash"]
        )
        self.assertTrue(result["changed"])
        revision = self.state / "revisions" / result["revision_id"]
        self.assertEqual((self.state / "current").resolve(), revision.resolve())
        self.assertIn(self.state.stat().st_mode & 0o7777, {0o2750, 0o750})
        self.assertIn(
            (self.state / "revisions").stat().st_mode & 0o7777,
            {0o2750, 0o750},
        )
        self.assertIn(revision.stat().st_mode & 0o7777, {0o2750, 0o750})

        snapshot = json.loads((revision / "snapshot.json").read_text())
        self.assertEqual(len(snapshot["rows"]), 2)
        blocked = next(row for row in snapshot["rows"] if row["runtime_id"] == "runtime-2")
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("missing_gateway_id", blocked["blocked_reasons"])
        self.assertIn("missing_monitor_host", blocked["blocked_reasons"])

        deployable = json.loads((revision / "inventory.json").read_text())
        self.assertEqual(len(deployable["gateways"]), 1)
        self.assertFalse(deployable["gateways"][0]["native_scrape_enabled"])
        scrape = yaml.safe_load((revision / "openclaw-gateways.yml").read_text())
        self.assertEqual(scrape["scrape_configs"], [])
        self.assertTrue(
            (revision / "openclaw-monitoring-patches" / "gateway-1.json").is_file()
        )
        patch_payload = json.loads(
            (
                revision
                / "openclaw-monitoring-patches"
                / "gateway-1.json"
            ).read_text()
        )
        headers = patch_payload["diagnostics"]["otel"]["headers"]
        self.assertEqual(
            headers["x-agentswatch-primary-agent-id"], "agent-runtime-1"
        )
        self.assertIn("x-agentswatch-agent-deployment-id", headers)

        rules = yaml.safe_load(
            (revision / "agentswatch-gateway-info.rules.yml").read_text()
        )["groups"][0]["rules"]
        self.assertEqual(len(rules), 2)
        labels = rules[0]["labels"]
        for key in (
            "runtime_id",
            "gateway_id",
            "primary_agent_id",
            "lobster_name",
            "server_display_id",
            "display_name",
        ):
            self.assertIn(key, labels)

        repeated = self.controller.sync("local-json")
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["revision_id"], result["revision_id"])

    def test_sync_refuses_empty_or_all_blocked_inventory(self):
        unsafe_inventories = (
            ([], "source snapshot is empty"),
            (
                [
                    {
                        **active_row(),
                        "gateway_id": "",
                        "host": "",
                    }
                ],
                "source snapshot has no deployable runtimes",
            ),
        )
        for rows, expected_blocker in unsafe_inventories:
            with self.subTest(expected_blocker=expected_blocker):
                self.write_inventory(rows)
                preview = self.controller.preview("local-json")
                self.assertFalse(preview["activation_allowed"])
                self.assertIn(expected_blocker, preview["activation_blockers"])
                with self.assertRaisesRegex(ValueError, "refusing to activate"):
                    self.controller.sync(
                        "local-json",
                        expected_snapshot_hash=preview["snapshot_hash"],
                    )
                self.assertFalse(self.controller.current_link.exists())

    def test_sync_refuses_removing_a_previously_deployable_runtime(self):
        first = self.controller.sync("local-json")
        self.assertTrue(first["changed"])
        self.write_inventory([active_row("runtime-3", "gateway-3", "替代龙虾")])

        preview = self.controller.preview("local-json")

        self.assertFalse(preview["activation_allowed"])
        self.assertIn(
            "source snapshot would remove 1 previously deployable runtimes",
            preview["activation_blockers"],
        )
        with self.assertRaisesRegex(
            ValueError, "ALLOW_DESTRUCTIVE_INVENTORY"
        ):
            self.controller.sync(
                "local-json", expected_snapshot_hash=preview["snapshot_hash"]
            )
        self.assertEqual(
            self.controller.current_link.resolve().name,
            first["revision_id"],
        )

        approved = InventoryController(
            replace(self.config, allow_destructive_inventory=True)
        )
        approved_preview = approved.preview("local-json")
        self.assertTrue(approved_preview["activation_allowed"])
        result = approved.sync(
            "local-json",
            expected_snapshot_hash=approved_preview["snapshot_hash"],
        )
        self.assertTrue(result["changed"])

    def test_full_teardown_requires_both_independent_overrides(self):
        self.controller.sync("local-json")
        self.write_inventory([])

        for allow_empty, allow_destructive, expected in (
            (False, False, False),
            (True, False, False),
            (False, True, False),
            (True, True, True),
        ):
            with self.subTest(
                allow_empty=allow_empty,
                allow_destructive=allow_destructive,
            ):
                controller = InventoryController(
                    replace(
                        self.config,
                        allow_empty_inventory=allow_empty,
                        allow_destructive_inventory=allow_destructive,
                    )
                )
                preview = controller.preview("local-json")
                self.assertEqual(preview["activation_allowed"], expected)

    def test_native_scrape_is_explicit_overlay_policy(self):
        self.overlay.write_text(
            json.dumps(
                {
                    "runtimes": {
                        "runtime-1": {
                            "native_scrape_enabled": True,
                            "scrape_host": "host.docker.internal",
                            "scrape_port": 26402,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self.controller.sync("local-json")
        revision = self.state / "revisions" / result["revision_id"]
        scrape = (revision / "openclaw-gateways.yml").read_text()
        self.assertIn("openclaw-gateway-gateway-1", scrape)
        self.assertIn("gateway-1.token", scrape)
        self.assertIn("host.docker.internal:26402", scrape)
        self.assertNotIn("10.0.0.11:16402", scrape)
        self.assertNotIn("gateway-2.token", scrape)
        patch = json.loads(
            (
                revision
                / "openclaw-monitoring-patches"
                / "gateway-1.json"
            ).read_text()
        )
        self.assertNotIn("gateway", patch)

    def test_native_scrape_rejects_invalid_overlay_port(self):
        self.overlay.write_text(
            json.dumps(
                {
                    "runtimes": {
                        "runtime-1": {
                            "native_scrape_enabled": True,
                            "scrape_port": 70000,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "outside 1..65535"):
            self.controller.preview("local-json")

    def test_overlay_requires_boolean_native_policy_and_string_host(self):
        invalid_values = (
            ({"native_scrape_enabled": "false"}, "must be a boolean"),
            ({"scrape_host": "   "}, "must be a non-empty string"),
            ({"scrape_host": ["host.docker.internal"]}, "must be a non-empty string"),
        )
        for values, message in invalid_values:
            with self.subTest(values=values):
                self.overlay.write_text(
                    json.dumps({"runtimes": {"runtime-1": values}}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, message):
                    self.controller.preview("local-json")

    def test_revision_artifacts_remain_group_readable_with_restrictive_umask(self):
        previous_umask = os.umask(0o077)
        try:
            result = self.controller.sync("local-json")
        finally:
            os.umask(previous_umask)

        revision = self.state / "revisions" / result["revision_id"]
        for directory in (
            self.state,
            self.state / "revisions",
            self.state / "staging",
            revision,
            revision / "openclaw-monitoring-patches",
        ):
            self.assertIn(directory.stat().st_mode & 0o7777, {0o2750, 0o750})
        for artifact in revision.rglob("*"):
            if artifact.is_file():
                self.assertEqual(artifact.stat().st_mode & 0o777, 0o640)

    def test_active_links_cannot_escape_direct_revision_directory(self):
        self.controller.ensure_layout()
        outside = self.root / "outside"
        outside.mkdir()
        self.controller.current_link.symlink_to(outside)
        self.controller.last_good_link.symlink_to(outside)

        self.assertIsNone(self.controller._active_revision_path())
        self.assertIsNone(self.controller._resolve_link(self.controller.last_good_link))

    def test_symlinked_revisions_root_is_unhealthy_and_rejected(self):
        self.state.mkdir()
        outside = self.root / "outside-revisions"
        revision = outside / "revision-1"
        revision.mkdir(parents=True)
        (self.state / "revisions").symlink_to(outside)
        (self.state / "current").symlink_to("revisions/revision-1")

        status = self.controller.status()

        self.assertFalse(status["healthy"])
        self.assertIn("symlinked work directory", status["state_error"])
        self.assertIsNone(self.controller._active_revision_path())

    def test_diff_covers_update_disable_orphan_and_noop(self):
        first = self.controller.sync("local-json")
        previous = json.loads(
            (self.state / "revisions" / first["revision_id"] / "snapshot.json").read_text()
        )["rows"]
        current = [dict(row) for row in previous]
        current[0]["lobster_name"] = "已改名"
        current[1]["source_present"] = True
        current[1]["deployable"] = False
        current.append({**current[0], "runtime_id": "runtime-3", "gateway_id": "gateway-3"})
        effective, actions = reconcile_snapshot(current, previous)
        action_by_id = {item["runtime_id"]: item["action"] for item in actions}
        self.assertEqual(action_by_id["runtime-1"], "update")
        self.assertEqual(action_by_id["runtime-2"], "retain_blocked")
        self.assertEqual(action_by_id["runtime-3"], "add")
        self.assertEqual(len(effective), 3)

        only_first = [dict(previous[0])]
        only_first[0]["deployable"] = False
        only_first[0]["state"] = "blocked"
        _, second_actions = reconcile_snapshot(only_first, previous)
        second = {item["runtime_id"]: item["action"] for item in second_actions}
        self.assertEqual(second["runtime-1"], "disable")
        self.assertEqual(second["runtime-2"], "orphan")

        _, noop_actions = reconcile_snapshot(previous, previous)
        noop_by_id = {item["runtime_id"]: item["action"] for item in noop_actions}
        self.assertEqual(noop_by_id["runtime-1"], "noop")
        self.assertEqual(noop_by_id["runtime-2"], "retain_blocked")

    def test_expected_hash_and_rollback_preserve_last_known_good(self):
        first = self.controller.sync("local-json")
        gateways = [active_row(name="改名后的龙虾")]
        self.write_inventory(gateways)
        preview = self.controller.preview("local-json")
        with self.assertRaises(SnapshotConflict):
            self.controller.sync("local-json", expected_snapshot_hash="0" * 64)
        second = self.controller.sync(
            "local-json", expected_snapshot_hash=preview["snapshot_hash"]
        )
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(
            (self.state / "last-known-good").resolve().name, first["revision_id"]
        )
        rollback = self.controller.rollback(first["revision_id"])
        self.assertTrue(rollback["changed"])
        self.assertEqual((self.state / "current").resolve().name, first["revision_id"])
        self.assertEqual(
            (self.state / "last-known-good").resolve().name, second["revision_id"]
        )

    def test_api_contract_bearer_auth_jobs_and_idempotency(self):
        app = create_app(self.controller)
        with patch.dict(
            os.environ,
            {"AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN": "control-secret"},
        ):
            client = TestClient(app)
            self.assertEqual(client.get("/api/v1/inventory/status").status_code, 401)
            headers = {"Authorization": "Bearer control-secret"}
            preview = client.post(
                "/api/v1/inventory/sync:preview",
                headers=headers,
                json={"source": "local-json"},
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            request_headers = {
                **headers,
                "Idempotency-Key": "local-first-sync",
            }
            first = client.post(
                "/api/v1/inventory/sync",
                headers=request_headers,
                json={
                    "source": "local-json",
                    "expected_snapshot_hash": preview.json()["snapshot_hash"],
                },
            )
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["status"], "succeeded")
            repeated = client.post(
                "/api/v1/inventory/sync",
                headers=request_headers,
                json={
                    "source": "local-json",
                    "expected_snapshot_hash": preview.json()["snapshot_hash"],
                },
            )
            self.assertEqual(repeated.json()["job_id"], first.json()["job_id"])
            job = client.get(
                f"/api/v1/jobs/{first.json()['job_id']}", headers=headers
            )
            self.assertEqual(job.status_code, 200)
            self.assertEqual(job.json()["result"]["changed"], True)
            refreshed_status = client.get(
                "/api/v1/inventory/status", headers=headers
            )
            self.assertEqual(refreshed_status.status_code, 200)
            self.assertEqual(
                refreshed_status.json()["jobs"][0]["job_id"],
                first.json()["job_id"],
            )
            metrics = client.get("/metrics", headers=headers)
            self.assertEqual(metrics.status_code, 200)
            self.assertIn("# TYPE agentswatch_gateway_info gauge", metrics.text)
            self.assertIn('runtime_id="runtime-1"', metrics.text)
            self.assertIn('lobster_name="龙虾一"', metrics.text)


if __name__ == "__main__":
    unittest.main()
