import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

from agentswatch_api.agent_operations import AgentOperationService, RUNTIME_AGENT_KEY
from agentswatch_api.server_management import ConflictError
from agentswatch_api.task_management import TaskManagementService


class _Cursor:
    def __init__(self, row=None, rows=None, rowcount=1):
        self.row = row
        self.rows = rows if rows is not None else ([] if row is None else [row])
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, run, host):
        self.run = run
        self.host = host
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))
        if "SELECT operation_code, parameters, requested_by" in sql:
            return _Cursor(self.run)
        if "SELECT host.*, host.target_plan AS plan" in sql:
            return _Cursor(self.host)
        return _Cursor(rowcount=1)


class _Database:
    def __init__(self, run, host):
        self.connection_object = _Connection(run, host)

    @contextmanager
    def connection(self):
        yield self.connection_object


class _ConnectionDatabase:
    def __init__(self, connection):
        self.connection_object = connection

    @contextmanager
    def connection(self):
        yield self.connection_object


class _Events:
    def __init__(self, *args, **kwargs):
        self.items = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def system(self, event_type, message, **kwargs):
        self.items.append((event_type, message, kwargs))

    def record(self, event):
        self.items.append(("ansible", event, {}))


def _remote(status="succeeded", result=None, error=None):
    return {
        "status": status,
        "result": result if result is not None else ({"deployed_agent_keys": ["stable-main"]} if status == "succeeded" else None),
        "error": error,
    }


class AgentOperationStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.run_id = uuid4()
        self.agent_id = uuid4()
        self.source_runtime_id = uuid4()
        self.target_runtime_id = uuid4()
        self.old_deployment_id = uuid4()
        self.new_deployment_id = uuid4()

    def migration_plan(self):
        definition = {
            "agent_id": str(self.agent_id),
            "name": "primary",
            "structure_role": "primary",
            "runtime_agent_key": "stable-main",
            "workspace_path": "/target/workspaces/stable-main",
            "model": "",
            "channel_type": "none",
            "channel_account_id": f"aw-{str(self.agent_id).replace('-', '')}",
        }
        return {
            "mode": "migrate_deployment",
            "expected_deployment_id": str(self.old_deployment_id),
            "expected_deployment_row_version": 7,
            "source_runtime": {"runtime_id": str(self.source_runtime_id)},
            "target_runtime": {"runtime_id": str(self.target_runtime_id)},
            "definitions": [definition],
            "retired_definitions": [],
            "source_cleanup_definitions": [definition],
            "source_apply_definitions": [definition],
            "source_retired_definitions": [],
            "source_apply_parameters": {
                "runtime_id": str(self.source_runtime_id),
                "runtime_agent_key": "stable-main",
            },
            "target_apply_parameters": {
                "runtime_id": str(self.target_runtime_id),
                "runtime_agent_key": "stable-main",
            },
        }

    def apply_plan(self, *, existing=False):
        plan = self.migration_plan()
        plan.update({
            "mode": "apply_deployment",
            "cleanup_reference_id": str(self.old_deployment_id),
            "target_runtime": {"runtime_id": str(self.target_runtime_id)},
            "retired_definitions": [],
            "require_existing_agent_keys": [],
            "allow_existing_agent_keys": [],
        })
        if existing:
            plan["expected_deployment_id"] = str(self.old_deployment_id)
            plan["expected_deployment_row_version"] = 7
            plan["apply_compensation"] = {
                "mode": "restore_existing",
                "source_apply_definitions": [{
                    **plan["definitions"][0],
                    "workspace_path": "/source/workspaces/stable-main",
                }],
                "source_retired_definitions": [],
                "source_apply_parameters": {
                    "runtime_id": str(self.target_runtime_id),
                    "runtime_agent_key": "stable-main",
                },
            }
        else:
            plan["expected_deployment_id"] = None
            plan.pop("expected_deployment_row_version", None)
            plan["apply_compensation"] = {"mode": "cleanup_unconfirmed"}
        return plan

    def _service(self, operation_code, plan):
        run = {
            "operation_code": operation_code,
            "parameters": {
                "expected_deployment_id": str(self.old_deployment_id),
                "runtime_id": str(self.target_runtime_id),
                "runtime_agent_key": "stable-main",
            },
            "requested_by": "tester",
            "status": "queued",
        }
        host = {
            "run_id": self.run_id,
            "server_id": uuid4(),
            "runtime_id": self.target_runtime_id,
            "agent_id": self.agent_id,
            "agent_deployment_id": self.old_deployment_id,
            "plan": plan,
        }
        database = _Database(run, host)
        return AgentOperationService(database=database), database

    @staticmethod
    def _final(database):
        return next(
            params for sql, params in reversed(database.connection_object.calls)
            if "UPDATE automation.operation_host_result" in sql and "result = %(result)s" in sql
        )

    def _execute(
        self, service, remote_side_effect, *, persist_effect=None,
        persist_apply_effect=None,
    ):
        def runtime(runtime_id, agent_id):
            return {"runtime_id": runtime_id, "agent_id": agent_id}
        with (
            patch("agentswatch_api.agent_operations.record_system_event"),
            patch("agentswatch_api.agent_operations.OperationEventRecorder", _Events),
            patch.object(service, "_terminal_replay_plan", return_value=service.database.connection_object.host["plan"]),
            patch.object(service, "_execution_runtime", side_effect=runtime),
            patch.object(service, "_validate_runtime_snapshot"),
            patch.object(service, "_run_remote", side_effect=remote_side_effect) as remote,
            patch.object(service, "_persist_apply", side_effect=persist_apply_effect) as persist_apply,
            patch.object(service, "_persist_migration", side_effect=persist_effect) as persist_migration,
            patch.object(service, "_persist_removal", side_effect=persist_effect) as persist_removal,
        ):
            if persist_apply_effect is None:
                persist_apply.return_value = self.new_deployment_id
            if persist_effect is None:
                persist_migration.return_value = self.new_deployment_id
                persist_removal.return_value = self.old_deployment_id
            service._execute_run(self.run_id)
        return remote

    def test_migration_stages_disabled_channel_then_switches_only_after_activation(self):
        service, database = self._service("agent.migrate_deployment", self.migration_plan())
        remote = self._execute(service, [_remote(), _remote(), _remote()])
        final = self._final(database)
        self.assertEqual(final["status"], "succeeded")
        self.assertTrue(final["result"].obj["relationship_switched"])
        self.assertEqual(final["deployment_id"], self.new_deployment_id)
        self.assertFalse(remote.call_args_list[0].kwargs["channel_enabled"])
        self.assertEqual(remote.call_args_list[1].args[0], "agent.remove_deployment")
        self.assertEqual(
            remote.call_args_list[1].kwargs["ownership_cleanup_mode"], "required",
        )
        self.assertTrue(remote.call_args_list[2].kwargs["channel_enabled"])

    def test_migration_stage_failure_keeps_relationship_and_cleans_target(self):
        service, database = self._service("agent.migrate_deployment", self.migration_plan())
        remote = self._execute(service, [_remote("failed", error="stage failed"), _remote()])
        final = self._final(database)
        self.assertEqual(final["status"], "failed")
        self.assertFalse(final["result"].obj["relationship_switched"])
        self.assertTrue(final["result"].obj["target_cleaned"])
        self.assertEqual(final["deployment_id"], self.old_deployment_id)
        self.assertEqual(len(remote.call_args_list), 2)
        self.assertEqual(
            remote.call_args_list[1].kwargs["ownership_cleanup_mode"],
            "compensation",
        )

    def test_first_apply_persist_failure_cleans_owned_target_and_keeps_relationship_empty(self):
        service, database = self._service(
            "agent.apply_deployment", self.apply_plan(existing=False),
        )
        database.connection_object.host["agent_deployment_id"] = None
        remote = self._execute(
            service, [_remote(), _remote(result={"skipped_no_ownership": False})],
            persist_apply_effect=RuntimeError("CAS conflict"),
        )
        final = self._final(database)
        result = final["result"].obj
        self.assertEqual(final["status"], "failed")
        self.assertIsNone(final["deployment_id"])
        self.assertTrue(result["target_cleaned"])
        self.assertFalse(result["recovery_required"])
        self.assertEqual(remote.call_args_list[1].args[0], "agent.remove_deployment")
        self.assertEqual(
            remote.call_args_list[1].kwargs["ownership_cleanup_mode"],
            "required",
        )

    def test_first_apply_persist_failure_reports_incomplete_cleanup(self):
        service, database = self._service(
            "agent.apply_deployment", self.apply_plan(existing=False),
        )
        database.connection_object.host["agent_deployment_id"] = None
        self._execute(
            service, [_remote(), _remote("failed", error="cleanup failed")],
            persist_apply_effect=RuntimeError("CAS conflict"),
        )
        result = self._final(database)["result"].obj
        self.assertFalse(result["target_cleaned"])
        self.assertTrue(result["recovery_required"])

    def test_first_apply_remote_unreachable_runs_zero_marker_compensation(self):
        service, database = self._service(
            "agent.apply_deployment", self.apply_plan(existing=False),
        )
        database.connection_object.host["agent_deployment_id"] = None
        remote = self._execute(
            service,
            [
                _remote("failed", error="unreachable"),
                _remote(result={"skipped_no_ownership": True}),
            ],
        )
        final = self._final(database)
        result = final["result"].obj
        self.assertEqual(final["status"], "failed")
        self.assertTrue(result["target_cleaned"])
        self.assertFalse(result["recovery_required"])
        self.assertEqual(
            remote.call_args_list[1].kwargs["ownership_cleanup_mode"],
            "compensation",
        )

    def test_first_apply_remote_failure_cleans_owned_marker_when_present(self):
        service, database = self._service(
            "agent.apply_deployment", self.apply_plan(existing=False),
        )
        database.connection_object.host["agent_deployment_id"] = None
        remote = self._execute(
            service,
            [
                _remote("failed", error="runner timeout"),
                _remote(result={"skipped_no_ownership": False}),
            ],
        )
        result = self._final(database)["result"].obj
        self.assertTrue(result["target_cleaned"])
        self.assertFalse(result["recovery_required"])
        self.assertEqual(
            remote.call_args_list[1].kwargs["ownership_cleanup_mode"],
            "compensation",
        )

    def test_first_apply_remote_failure_reports_incomplete_external_compensation(self):
        service, database = self._service(
            "agent.apply_deployment", self.apply_plan(existing=False),
        )
        database.connection_object.host["agent_deployment_id"] = None
        self._execute(
            service,
            [
                _remote("failed", error="runner timeout"),
                _remote("failed", error="ownership partial"),
            ],
        )
        result = self._final(database)["result"].obj
        self.assertFalse(result["target_cleaned"])
        self.assertTrue(result["recovery_required"])

    def test_existing_apply_remote_failure_restores_only_owned_source(self):
        plan = self.apply_plan(existing=True)
        service, database = self._service("agent.apply_deployment", plan)
        remote = self._execute(
            service, [_remote("failed", error="runner timeout"), _remote()],
        )
        result = self._final(database)["result"].obj
        self.assertTrue(result["source_restored"])
        self.assertFalse(result["recovery_required"])
        self.assertEqual(remote.call_args_list[1].args[0], "agent.apply_deployment")
        self.assertEqual(
            remote.call_args_list[1].kwargs["require_owned_agent_keys"],
            ["stable-main"],
        )

    def test_existing_apply_persist_failure_restores_previous_remote_definition(self):
        plan = self.apply_plan(existing=True)
        retired_agent_id = uuid4()
        plan["apply_compensation"]["source_retired_definitions"] = [{
            "agent_id": str(retired_agent_id),
            "name": "retired child",
            "structure_role": "subagent",
            "runtime_agent_key": "retired-child",
            "workspace_path": "/source/workspaces/retired-child",
            "channel_type": "none",
            "channel_account_id": f"aw-{str(retired_agent_id).replace('-', '')}",
        }]
        service, database = self._service("agent.apply_deployment", plan)
        remote = self._execute(
            service, [_remote(), _remote()],
            persist_apply_effect=RuntimeError("CAS conflict"),
        )
        final = self._final(database)
        result = final["result"].obj
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["deployment_id"], self.old_deployment_id)
        self.assertTrue(result["source_restored"])
        self.assertFalse(result["recovery_required"])
        self.assertEqual(remote.call_args_list[1].args[0], "agent.apply_deployment")
        self.assertEqual(
            remote.call_args_list[1].args[2],
            plan["apply_compensation"]["source_apply_definitions"],
        )
        self.assertEqual(
            remote.call_args_list[1].kwargs["require_owned_agent_keys"],
            ["stable-main", "retired-child"],
        )

    def test_migration_activation_failure_restores_source_and_cleans_target(self):
        service, database = self._service("agent.migrate_deployment", self.migration_plan())
        remote = self._execute(
            service,
            [_remote(), _remote(), _remote("failed", error="activate failed"), _remote(), _remote()],
        )
        final = self._final(database)
        result = final["result"].obj
        self.assertEqual(final["status"], "failed")
        self.assertTrue(result["source_restored"])
        self.assertTrue(result["target_cleaned"])
        self.assertFalse(result["recovery_required"])
        self.assertEqual(
            remote.call_args_list[3].kwargs["require_owned_agent_keys"],
            ["stable-main"],
        )
        self.assertEqual(
            remote.call_args_list[4].kwargs["ownership_cleanup_mode"], "required",
        )

    def test_migration_cas_failure_compensates_and_keeps_old_deployment(self):
        service, database = self._service("agent.migrate_deployment", self.migration_plan())
        self._execute(
            service, [_remote(), _remote(), _remote(), _remote(), _remote()],
            persist_effect=RuntimeError("CAS conflict"),
        )
        final = self._final(database)
        result = final["result"].obj
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["deployment_id"], self.old_deployment_id)
        self.assertTrue(result["source_restored"])
        self.assertTrue(result["target_cleaned"])

    def test_remove_persist_failure_restores_source_and_keeps_relationship(self):
        plan = self.migration_plan()
        plan["mode"] = "remove_deployment"
        plan.pop("target_runtime")
        service, database = self._service("agent.remove_deployment", plan)
        remote = self._execute(
            service, [_remote(), _remote()], persist_effect=RuntimeError("CAS conflict"),
        )
        final = self._final(database)
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["deployment_id"], self.old_deployment_id)
        self.assertTrue(final["result"].obj["source_restored"])
        self.assertEqual(
            remote.call_args_list[0].kwargs["ownership_cleanup_mode"], "required",
        )

    def test_remove_remote_failure_never_persists_relationship_change(self):
        plan = self.migration_plan()
        plan["mode"] = "remove_deployment"
        plan.pop("target_runtime")
        service, database = self._service("agent.remove_deployment", plan)
        remote = self._execute(
            service, [_remote("failed", error="cleanup failed"), _remote()],
        )
        final = self._final(database)
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["deployment_id"], self.old_deployment_id)
        self.assertTrue(final["result"].obj["source_restored"])
        self.assertEqual(
            remote.call_args_list[1].kwargs["require_owned_agent_keys"],
            ["stable-main"],
        )

    def test_unmanaged_import_removal_only_unlinks_metadata_without_remote_execution(self):
        plan = self.migration_plan()
        plan["mode"] = "remove_unmanaged"
        plan.pop("target_runtime")
        plan["source_cleanup_definitions"] = []
        service, database = self._service("agent.remove_deployment", plan)
        remote = self._execute(service, [])
        final = self._final(database)
        result = final["result"].obj
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["deployment_id"], self.old_deployment_id)
        self.assertEqual(remote.call_count, 0)
        self.assertTrue(result["relationship_removed"])
        self.assertTrue(result["remote_cleanup_skipped"])
        self.assertTrue(result["remote_cleanup_not_required"])
        self.assertTrue(result["unmanaged_mapping"])
        self.assertFalse(result["source_restored"])

    def test_nonverified_existing_member_candidate_key_can_be_corrected_and_promoted(self):
        member_id = uuid4()
        connection = MagicMock()

        def execute(sql, params=None):
            if "SELECT deployment_member_id" in sql:
                return _Cursor(rows=[{
                    "deployment_member_id": member_id,
                    "agent_id": self.agent_id,
                    "runtime_agent_key": "candidate-main",
                    "mapping_status": "unresolved",
                    "config_ref": None,
                    "row_version": 4,
                }])
            return _Cursor(rowcount=1)

        connection.execute.side_effect = execute
        deployment_id = AgentOperationService._persist_deployment(
            connection,
            {
                "agent_deployment_id": self.old_deployment_id,
                "agent_id": self.agent_id,
                "runtime_id": self.source_runtime_id,
            },
            [self.migration_plan()["definitions"][0]],
            {
                "deployed_agent_keys": ["stable-main"],
                "agent_workspaces": {
                    "stable-main": "/observed/workspaces/stable-main",
                },
            },
            "tester",
        )
        self.assertEqual(deployment_id, self.old_deployment_id)
        update_calls = [call for call in connection.execute.call_args_list
                        if "UPDATE asset.agent_deployment_member" in call.args[0]]
        self.assertEqual(len(update_calls), 1)
        update_parameters = update_calls[0].args[1]
        self.assertEqual(update_parameters["runtime_key"], "stable-main")
        self.assertEqual(update_parameters["expected_runtime_key"], "candidate-main")
        self.assertEqual(
            update_parameters["config_ref"], "/observed/workspaces/stable-main",
        )
        self.assertEqual(update_parameters["row_version"], 4)

    def test_nonverified_member_promotion_rejects_cas_drift(self):
        member_id = uuid4()
        connection = MagicMock()

        def execute(sql, params=None):
            if "SELECT deployment_member_id" in sql:
                return _Cursor(rows=[{
                    "deployment_member_id": member_id,
                    "agent_id": self.agent_id,
                    "runtime_agent_key": None,
                    "mapping_status": "unresolved",
                    "config_ref": None,
                    "row_version": 2,
                }])
            if "UPDATE asset.agent_deployment_member" in sql:
                return _Cursor(rowcount=0)
            return _Cursor(rowcount=1)

        connection.execute.side_effect = execute
        with self.assertRaisesRegex(ConflictError, "changed before it could be verified"):
            AgentOperationService._persist_deployment(
                connection,
                {
                    "agent_deployment_id": self.old_deployment_id,
                    "agent_id": self.agent_id,
                    "runtime_id": self.source_runtime_id,
                },
                [self.migration_plan()["definitions"][0]],
                {
                    "deployed_agent_keys": ["stable-main"],
                    "agent_workspaces": {
                        "stable-main": "/observed/workspaces/stable-main",
                    },
                },
                "tester",
            )

    def test_persist_apply_rejects_existing_deployment_row_version_drift(self):
        connection = MagicMock()

        def execute(sql, params=None):
            if "FROM asset.runtime_instance runtime" in sql:
                return _Cursor(row={"runtime_id": self.target_runtime_id})
            if "FROM asset.agent_deployment" in sql and "FOR UPDATE" in sql:
                return _Cursor(row={
                    "agent_deployment_id": self.old_deployment_id,
                    "primary_agent_id": self.agent_id,
                    "runtime_id": self.target_runtime_id,
                    "lifecycle_status": "active",
                    "row_version": 8,
                })
            return _Cursor(rowcount=1)

        connection.execute.side_effect = execute
        service = AgentOperationService(database=_ConnectionDatabase(connection))
        plan = self.apply_plan(existing=True)
        with (
            patch.object(service, "_validate_runtime_snapshot"),
            patch.object(service, "_persist_deployment") as persist,
            self.assertRaisesRegex(ConflictError, "changed before synchronization"),
        ):
            service._persist_apply(
                {
                    "agent_id": self.agent_id,
                    "agent_deployment_id": self.old_deployment_id,
                },
                plan, {}, "tester",
            )
        persist.assert_not_called()

    def test_persist_first_apply_rejects_new_active_or_occupied_relationship(self):
        connection = MagicMock()

        def execute(sql, params=None):
            if "FROM asset.runtime_instance runtime" in sql:
                return _Cursor(row={"runtime_id": self.target_runtime_id})
            if "WHERE primary_agent_id" in sql:
                return _Cursor(row={"agent_deployment_id": uuid4()})
            if "WHERE runtime_id" in sql and "FROM asset.agent_deployment" in sql:
                return _Cursor(row=None)
            return _Cursor(rowcount=1)

        connection.execute.side_effect = execute
        service = AgentOperationService(database=_ConnectionDatabase(connection))
        with (
            patch.object(service, "_validate_runtime_snapshot"),
            patch.object(service, "_persist_deployment") as persist,
            self.assertRaisesRegex(ConflictError, "changed before first apply"),
        ):
            service._persist_apply(
                {"agent_id": self.agent_id, "agent_deployment_id": None},
                self.apply_plan(existing=False), {}, "tester",
            )
        persist.assert_not_called()

    def test_disabled_stage_patch_keeps_same_account_identity_without_enabling_it(self):
        configuration_id = uuid4()
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "agent_channel_configuration_id": configuration_id,
            "channel_type": "feishu",
            "application_name": "primary",
            "app_id": "cli-primary",
            "encrypted_app_secret": "ciphertext",
            "enabled": True,
            "row_version": 3,
        }
        cipher = MagicMock()
        cipher.decrypt.return_value = {"app_secret": "secret"}
        with patch("agentswatch_api.agent_operations.CredentialCipher", return_value=cipher):
            value = AgentOperationService._build_channel_patch(
                connection,
                [{
                    "name": "primary",
                    "channel_type": "feishu",
                    "channel_configuration_id": str(configuration_id),
                    "channel_row_version": 3,
                    "channel_app_id": "cli-primary",
                    "channel_account_id": "aw-primary",
                }],
                enabled=False,
            )
        self.assertFalse(value["channels"]["feishu"]["accounts"]["aw-primary"]["enabled"])
        self.assertEqual(value["channels"]["feishu"]["accounts"]["aw-primary"]["appId"], "cli-primary")
        self.assertNotIn("enabled", value["channels"]["feishu"])
        self.assertNotIn("connectionMode", value["channels"]["feishu"])

    def test_migration_or_removal_requires_source_member_sync(self):
        current = [{"agent_id": "primary", "runtime_agent_key": "stable-main"}]
        desired = [{"agent_id": "primary", "runtime_agent_key": "stable-main"}]
        AgentOperationService._assert_source_synchronized(current, desired, [])
        with self.assertRaisesRegex(RuntimeError, "not synchronized"):
            AgentOperationService._assert_source_synchronized(
                current,
                desired + [{"agent_id": "new-child", "runtime_agent_key": "stable-child"}],
                [],
            )
        with self.assertRaisesRegex(RuntimeError, "not synchronized"):
            AgentOperationService._assert_source_synchronized(
                current, desired, [{"agent_id": "retired-child"}],
            )

    def test_planned_or_missing_member_mapping_is_not_treated_as_verified(self):
        self.assertFalse(AgentOperationService._members_are_verified([]))
        self.assertFalse(AgentOperationService._members_are_verified([{
            "mapping_status": "planned",
            "runtime_agent_key": "candidate-main",
        }]))
        self.assertFalse(AgentOperationService._members_are_verified([{
            "mapping_status": "verified",
            "runtime_agent_key": None,
        }]))
        self.assertTrue(AgentOperationService._members_are_verified([{
            "mapping_status": "verified",
            "runtime_agent_key": "stable-main",
        }]))
        with self.assertRaisesRegex(ConflictError, "no current members"):
            AgentOperationService._assert_active_deployment_has_members(
                {
                    "agent_deployment_id": self.old_deployment_id,
                    "primary_agent_id": self.agent_id,
                }, [],
            )
        with self.assertRaisesRegex(ConflictError, "invalid current primary"):
            AgentOperationService._assert_active_deployment_has_members(
                {
                    "agent_deployment_id": self.old_deployment_id,
                    "primary_agent_id": self.agent_id,
                }, [{
                    "agent_id": uuid4(),
                    "member_role": "primary",
                }],
            )
        with self.assertRaisesRegex(ConflictError, "invalid current primary"):
            AgentOperationService._assert_active_deployment_has_members(
                {
                    "agent_deployment_id": self.old_deployment_id,
                    "primary_agent_id": self.agent_id,
                }, [{
                    "agent_id": uuid4(),
                    "member_role": "subagent",
                }],
            )
        AgentOperationService._assert_active_deployment_has_members(
            {
                "agent_deployment_id": self.old_deployment_id,
                "primary_agent_id": self.agent_id,
            }, [{
                "agent_id": self.agent_id,
                "member_role": "primary",
            }],
        )
        AgentOperationService._assert_active_deployment_has_members(None, [])

    def test_child_key_stays_within_openclaw_key_limit(self):
        child_key = AgentOperationService._child_key("a" * 64, uuid4())
        self.assertLessEqual(len(child_key), 64)
        self.assertIsNotNone(RUNTIME_AGENT_KEY.fullmatch(child_key))

    def test_unresolved_primary_cannot_adopt_a_verified_child_key(self):
        child_id = uuid4()
        connection = MagicMock()
        connection.execute.side_effect = [
            _Cursor(rows=[
                {
                    "agent_id": self.agent_id,
                    "runtime_agent_key": "candidate-primary",
                    "config_ref": None,
                    "mapping_status": "unresolved",
                    "name": "primary",
                    "lifecycle_status": "testing",
                },
                {
                    "agent_id": child_id,
                    "runtime_agent_key": "verified-child",
                    "config_ref": "/workspaces/verified-child",
                    "mapping_status": "verified",
                    "name": "child",
                    "lifecycle_status": "active",
                },
            ]),
            _Cursor(rows=[
                {
                    "agent_id": self.agent_id,
                    "name": "primary",
                    "structure_role": "primary",
                    "channel_type": "none",
                    "channel_configuration_id": None,
                    "channel_application_name": None,
                    "channel_app_id": None,
                    "channel_row_version": None,
                },
                {
                    "agent_id": child_id,
                    "name": "child",
                    "structure_role": "subagent",
                    "channel_type": "none",
                    "channel_configuration_id": None,
                    "channel_application_name": None,
                    "channel_app_id": None,
                    "channel_row_version": None,
                },
            ]),
        ]
        with self.assertRaisesRegex(ConflictError, "unique runtime agent keys"):
            AgentOperationService._definitions(
                connection,
                {"agent_id": self.agent_id},
                {"state_path": "/runtime-state"},
                {"runtime_agent_key": "verified-child"},
            )

    def test_remote_definition_validation_prevents_runner_execution(self):
        service = AgentOperationService(database=MagicMock())
        duplicate = {
            "agent_id": str(self.agent_id),
            "runtime_agent_key": "duplicate-key",
        }
        with patch("agentswatch_api.agent_operations.AutomationRunner.run") as run:
            result = service._run_remote(
                "agent.apply_deployment", {"agent_id": self.agent_id},
                [duplicate, {**duplicate}], [], {},
                _Events(), "invalid_contract",
            )
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicate runtime agent keys", result["error"])
        run.assert_not_called()

    def test_moved_or_promoted_agent_cannot_join_until_previous_primary_is_synchronized(self):
        previous_primary_id = uuid4()
        for structure_role in ("subagent", "primary"):
            with self.subTest(structure_role=structure_role):
                connection = MagicMock()
                connection.execute.return_value = _Cursor(row={
                    "agent_id": self.agent_id,
                    "primary_agent_id": previous_primary_id,
                    "agent_deployment_id": self.old_deployment_id,
                })
                with self.assertRaisesRegex(
                    ConflictError, "previous primary deployment",
                ):
                    AgentOperationService._assert_no_foreign_active_members(
                        connection,
                        uuid4(),
                        [{
                            "agent_id": str(self.agent_id),
                            "structure_role": structure_role,
                        }],
                    )

        connection = MagicMock()
        connection.execute.return_value = _Cursor(row=None)
        AgentOperationService._assert_no_foreign_active_members(
            connection,
            self.agent_id,
            [{"agent_id": str(self.agent_id), "structure_role": "primary"}],
        )

    def test_terminal_replay_recognizes_committed_apply_migrate_and_unmanaged_remove(self):
        apply_row = {
            "agent_id": self.agent_id,
            "runtime_id": self.target_runtime_id,
            "agent_deployment_id": None,
            "plan": {"mode": "apply_deployment"},
        }
        service = AgentOperationService(database=_ConnectionDatabase(MagicMock()))
        with (
            patch.object(service, "_active_deployment", return_value={
                "agent_deployment_id": self.new_deployment_id,
                "runtime_id": self.target_runtime_id,
            }),
            patch.object(service, "_deployment_matches_definitions", return_value=True),
        ):
            replay = service._terminal_replay_plan(
                apply_row, "agent.apply_deployment",
            )
        self.assertEqual(replay["mode"], "already_applied")
        self.assertEqual(replay["active_deployment_id"], str(self.new_deployment_id))

        migrate_connection = MagicMock()
        migrate_connection.execute.return_value = _Cursor(row={
            "lifecycle_status": "inactive",
            "runtime_id": self.source_runtime_id,
            "row_version": 8,
        })
        service = AgentOperationService(
            database=_ConnectionDatabase(migrate_connection),
        )
        migrate_row = {
            "agent_id": self.agent_id,
            "runtime_id": self.target_runtime_id,
            "agent_deployment_id": self.old_deployment_id,
            "plan": self.migration_plan(),
        }
        with (
            patch.object(service, "_active_deployment", return_value={
                "agent_deployment_id": self.new_deployment_id,
                "runtime_id": self.target_runtime_id,
            }),
            patch.object(service, "_deployment_matches_definitions", return_value=True),
        ):
            replay = service._terminal_replay_plan(
                migrate_row, "agent.migrate_deployment",
            )
        self.assertEqual(replay["mode"], "already_migrated")
        self.assertEqual(replay["active_deployment_id"], str(self.new_deployment_id))

        remove_connection = MagicMock()
        remove_connection.execute.return_value = _Cursor(row={
            "lifecycle_status": "inactive",
            "runtime_id": self.source_runtime_id,
            "row_version": 8,
        })
        service = AgentOperationService(
            database=_ConnectionDatabase(remove_connection),
        )
        remove_plan = self.migration_plan()
        remove_plan["mode"] = "remove_unmanaged"
        with (
            patch.object(service, "_active_deployment", return_value=None),
            patch.object(service, "_deployment_member_state", return_value=[]),
        ):
            replay = service._terminal_replay_plan(
                {**migrate_row, "plan": remove_plan},
                "agent.remove_deployment",
            )
        self.assertEqual(replay["mode"], "already_removed")
        self.assertTrue(replay["unmanaged_mapping"])

    def test_terminal_replay_does_not_short_circuit_an_active_source_relationship(self):
        connection = MagicMock()
        connection.execute.return_value = _Cursor(row={
            "lifecycle_status": "active",
            "runtime_id": self.source_runtime_id,
            "row_version": 7,
        })
        service = AgentOperationService(database=_ConnectionDatabase(connection))
        row = {
            "agent_id": self.agent_id,
            "runtime_id": self.target_runtime_id,
            "agent_deployment_id": self.old_deployment_id,
            "plan": self.migration_plan(),
        }
        with patch.object(service, "_active_deployment", return_value={
            "agent_deployment_id": self.old_deployment_id,
            "runtime_id": self.source_runtime_id,
        }):
            replay = service._terminal_replay_plan(
                row, "agent.migrate_deployment",
            )
        self.assertEqual(replay["mode"], "migrate_deployment")

    def test_terminal_replay_existing_sync_reexecutes_only_when_parent_version_matches(self):
        plan = self.apply_plan(existing=True)
        row = {
            "agent_id": self.agent_id,
            "runtime_id": self.target_runtime_id,
            "agent_deployment_id": self.old_deployment_id,
            "plan": plan,
        }
        service = AgentOperationService(database=_ConnectionDatabase(MagicMock()))
        with patch.object(service, "_active_deployment", return_value={
            "agent_deployment_id": self.old_deployment_id,
            "runtime_id": self.target_runtime_id,
            "row_version": 7,
        }):
            replay = service._terminal_replay_plan(
                row, "agent.apply_deployment",
            )
        self.assertEqual(replay["mode"], "apply_deployment")

        with (
            patch.object(service, "_active_deployment", return_value={
                "agent_deployment_id": self.old_deployment_id,
                "runtime_id": self.target_runtime_id,
                "row_version": 8,
            }),
            self.assertRaisesRegex(ConflictError, "drifted"),
        ):
            service._terminal_replay_plan(row, "agent.apply_deployment")

    def test_terminal_replay_rejects_same_runtime_with_member_or_key_drift(self):
        apply_plan = self.apply_plan(existing=False)
        apply_row = {
            "agent_id": self.agent_id,
            "runtime_id": self.target_runtime_id,
            "agent_deployment_id": None,
            "plan": apply_plan,
        }
        service = AgentOperationService(database=_ConnectionDatabase(MagicMock()))
        with (
            patch.object(service, "_active_deployment", return_value={
                "agent_deployment_id": self.new_deployment_id,
                "runtime_id": self.target_runtime_id,
            }),
            patch.object(service, "_deployment_matches_definitions", return_value=False),
            self.assertRaisesRegex(ConflictError, "different active deployment"),
        ):
            service._terminal_replay_plan(apply_row, "agent.apply_deployment")

        connection = MagicMock()
        connection.execute.return_value = _Cursor(row={
            "lifecycle_status": "inactive",
            "runtime_id": self.source_runtime_id,
            "row_version": 8,
        })
        service = AgentOperationService(database=_ConnectionDatabase(connection))
        migrate_row = {
            "agent_id": self.agent_id,
            "runtime_id": self.target_runtime_id,
            "agent_deployment_id": self.old_deployment_id,
            "plan": self.migration_plan(),
        }
        with (
            patch.object(service, "_active_deployment", return_value={
                "agent_deployment_id": self.new_deployment_id,
                "runtime_id": self.target_runtime_id,
            }),
            patch.object(service, "_deployment_matches_definitions", return_value=False),
            self.assertRaisesRegex(ConflictError, "no longer matches"),
        ):
            service._terminal_replay_plan(
                migrate_row, "agent.migrate_deployment",
            )

    def test_deployment_match_requires_exact_verified_member_role_key_and_workspace(self):
        definition = self.migration_plan()["definitions"][0]
        connection = MagicMock()
        connection.execute.return_value = _Cursor(rows=[{
            "agent_id": self.agent_id,
            "member_role": "primary",
            "runtime_agent_key": "stable-main",
            "mapping_status": "verified",
            "config_ref": "/observed/workspaces/stable-main",
        }])
        self.assertTrue(AgentOperationService._deployment_matches_definitions(
            connection, self.new_deployment_id, [definition],
        ))
        connection.execute.return_value = _Cursor(rows=[{
            "agent_id": self.agent_id,
            "member_role": "subagent",
            "runtime_agent_key": "stable-main",
            "mapping_status": "verified",
            "config_ref": "/observed/workspaces/stable-main",
        }])
        self.assertFalse(AgentOperationService._deployment_matches_definitions(
            connection, self.new_deployment_id, [definition],
        ))

    def test_startup_requeues_only_relationship_operations_and_returns_all_queued(self):
        apply_run_id = uuid4()
        migrate_run_id = uuid4()
        remove_run_id = uuid4()
        interrupted_server_run_id = uuid4()
        preexisting_queued_run_id = uuid4()
        connection = MagicMock()

        def execute(sql, params=None):
            if "WHERE status = 'running'" in sql and "SELECT run_id" in sql:
                return _Cursor(rows=[
                    {
                        "run_id": apply_run_id,
                        "operation_code": "agent.apply_deployment",
                        "target_type": "agent",
                    },
                    {
                        "run_id": migrate_run_id,
                        "operation_code": "agent.migrate_deployment",
                        "target_type": "agent",
                    },
                    {
                        "run_id": remove_run_id,
                        "operation_code": "agent.remove_deployment",
                        "target_type": "agent",
                    },
                    {
                        "run_id": interrupted_server_run_id,
                        "operation_code": "server.check_connection",
                        "target_type": "server",
                    },
                ])
            if "WHERE status = 'queued'" in sql and "SELECT run_id" in sql:
                return _Cursor(rows=[
                    {"run_id": preexisting_queued_run_id, "target_type": "server"},
                    {"run_id": apply_run_id, "target_type": "agent"},
                    {"run_id": migrate_run_id, "target_type": "agent"},
                    {"run_id": remove_run_id, "target_type": "agent"},
                ])
            return _Cursor(rowcount=1)

        connection.execute.side_effect = execute
        service = TaskManagementService(database=_ConnectionDatabase(connection))
        with (
            patch("agentswatch_api.task_management.record_system_event") as recorded,
            patch("agentswatch_api.task_management.finalize_unhandled_run") as finalized,
        ):
            queued = service.recover_for_startup()
        self.assertEqual(
            [item["run_id"] for item in queued],
            [preexisting_queued_run_id, apply_run_id, migrate_run_id, remove_run_id],
        )
        self.assertEqual(
            {item.args[0] for item in recorded.call_args_list},
            {apply_run_id, migrate_run_id, remove_run_id},
        )
        finalized.assert_called_once_with(
            interrupted_server_run_id,
            "AgentsWatch API 重启，上一执行进程已中断",
            service.database,
        )
        requeued_run_ids = {
            item.args[1]["run_id"]
            for item in connection.execute.call_args_list
            if "UPDATE automation.operation_run" in item.args[0]
            and "status = 'queued'" in item.args[0]
        }
        self.assertEqual(
            requeued_run_ids, {apply_run_id, migrate_run_id, remove_run_id},
        )


if __name__ == "__main__":
    unittest.main()
