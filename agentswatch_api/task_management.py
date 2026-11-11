from uuid import UUID

from .database import WriteDatabase
from .operation_events import finalize_unhandled_run, record_system_event
from .server_management import ConflictError, NotFoundError


class TaskManagementService:
    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    def cancel(self, run_id: UUID, actor="local-user"):
        with self.database.connection() as connection:
            run = connection.execute(
                """
                SELECT status::text AS status FROM automation.operation_run
                WHERE run_id = %(run_id)s FOR UPDATE
                """,
                {"run_id": run_id},
            ).fetchone()
            if run is None:
                raise NotFoundError("task not found")
            if run["status"] == "cancelled":
                return
            if run["status"] != "queued":
                raise ConflictError("only queued tasks can be cancelled safely")
            connection.execute(
                """
                UPDATE automation.operation_run
                SET status = 'cancelled', cancellation_requested_at = now(),
                    cancellation_requested_by = %(actor)s, finished_at = now()
                WHERE run_id = %(run_id)s
                """,
                {"actor": actor, "run_id": run_id},
            )
            connection.execute(
                """
                UPDATE automation.operation_host_result
                SET status = 'skipped', error_message = '任务在执行前已取消',
                    finished_at = now()
                WHERE run_id = %(run_id)s AND status = 'queued'
                """,
                {"run_id": run_id},
            )
        record_system_event(
            run_id, "task_cancelled", "任务在开始执行前已取消。",
            phase="finalize", level="warning", data={"requested_by": actor},
            database=self.database,
        )

    def retry_specification(self, run_id: UUID):
        with self.database.connection() as connection:
            run = connection.execute(
                """
                SELECT operation_code, target_type, status::text AS status, parameters
                FROM automation.operation_run WHERE run_id = %(run_id)s
                """,
                {"run_id": run_id},
            ).fetchone()
            if run is None:
                raise NotFoundError("task not found")
            if run["status"] in {"queued", "running"}:
                raise ConflictError("running tasks cannot be retried")
            rows = connection.execute(
                """
                SELECT server_id, runtime_id, agent_id
                FROM automation.operation_host_result
                WHERE run_id = %(run_id)s
                  AND status IN ('failed', 'unreachable', 'skipped')
                ORDER BY target_key
                """,
                {"run_id": run_id},
            ).fetchall()
            if not rows:
                raise ConflictError("task has no failed or skipped targets to retry")
        target_field = {
            "server": "server_ids", "runtime": "runtime_ids", "agent": "agent_ids",
        }[run["target_type"]]
        row_field = {
            "server": "server_id", "runtime": "runtime_id", "agent": "agent_id",
        }[run["target_type"]]
        target_ids = [row[row_field] for row in rows if row[row_field] is not None]
        if not target_ids:
            raise ConflictError("failed task targets are no longer available")
        return {
            "operation_code": run["operation_code"],
            "target_type": run["target_type"],
            target_field: target_ids,
            "parameters": run["parameters"] or {},
        }

    def link_retry(self, run_id: UUID, original_run_id: UUID):
        with self.database.connection() as connection:
            result = connection.execute(
                """
                UPDATE automation.operation_run SET retry_of_run_id = %(original_run_id)s
                WHERE run_id = %(run_id)s AND retry_of_run_id IS NULL
                """,
                {"run_id": run_id, "original_run_id": original_run_id},
            )
            if result.rowcount != 1:
                raise ConflictError("retry task could not be linked to its original task")

    def recover_for_startup(self):
        """Recover idempotent relationship work and fail other interrupted executions."""
        with self.database.connection() as connection:
            running = list(connection.execute(
                """
                SELECT run_id, operation_code, target_type::text AS target_type
                FROM automation.operation_run
                WHERE status = 'running'
                ORDER BY created_at
                """
            ).fetchall())
            recoverable = {
                "agent.apply_deployment",
                "agent.migrate_deployment",
                "agent.remove_deployment",
            }
            recovered_ids = []
            for row in running:
                if row["target_type"] != "agent" or row["operation_code"] not in recoverable:
                    continue
                connection.execute(
                    """
                    UPDATE automation.operation_run
                    SET status = 'queued', started_at = NULL, finished_at = NULL,
                        succeeded_count = 0, failed_count = 0
                    WHERE run_id = %(run_id)s AND status = 'running'
                    """,
                    {"run_id": row["run_id"]},
                )
                connection.execute(
                    """
                    UPDATE automation.operation_host_result
                    SET status = 'queued', result = NULL, error_message = NULL,
                        started_at = NULL, finished_at = NULL
                    WHERE run_id = %(run_id)s
                    """,
                    {"run_id": row["run_id"]},
                )
                recovered_ids.append(row["run_id"])
        for row in running:
            run_id = row["run_id"]
            if run_id in recovered_ids:
                record_system_event(
                    run_id, "task_requeued_after_restart",
                    "API 重启后重新排队可恢复的智能体部署关系任务。",
                    phase="dispatch", level="warning", database=self.database,
                )
                continue
            finalize_unhandled_run(
                run_id, "AgentsWatch API 重启，上一执行进程已中断", self.database,
            )
        with self.database.connection() as connection:
            return list(connection.execute(
                """
                SELECT run_id, target_type::text AS target_type
                FROM automation.operation_run
                WHERE status = 'queued'
                ORDER BY created_at, run_id
                """
            ).fetchall())
