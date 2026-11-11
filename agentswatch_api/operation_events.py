import logging
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agentswatch_automation.redaction import redact_sensitive_data, redact_sensitive_text

from .database import WriteDatabase


LOGGER = logging.getLogger(__name__)
MAX_EVENTS_PER_RUN = 5000
MAX_STREAM_EVENTS_PER_RUN = MAX_EVENTS_PER_RUN - 2
MAX_STDOUT_BYTES = 16000
TERMINAL_EVENT_TYPES = {"task_completed", "task_cancelled", "task_failed"}


def _truncate_utf8(value, limit=MAX_STDOUT_BYTES):
    if value is None:
        return None
    encoded = str(value).encode("utf-8")
    if len(encoded) <= limit:
        return encoded.decode("utf-8")
    suffix = "\n…输出已截断"
    suffix_bytes = suffix.encode("utf-8")
    return encoded[:limit - len(suffix_bytes)].decode("utf-8", errors="ignore") + suffix


class OperationEventRecorder:
    """Writes a bounded, sanitized execution timeline without exposing Runner artifacts."""

    def __init__(self, run_id: UUID | str, database=None):
        self.run_id = UUID(str(run_id))
        self.database = database or WriteDatabase()
        self.connection = None
        self.sequence = 0
        self.failed = False

    def __enter__(self):
        try:
            self.connection = psycopg.connect(
                self.database.dsn, row_factory=dict_row, autocommit=True,
            )
        except Exception as exc:
            self.failed = True
            LOGGER.exception("operation event connection failed for %s: %s", self.run_id, exc)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def system(self, event_type, message, phase="dispatch", level="info", target_key=None, data=None):
        self.record({
            "phase": phase,
            "event_type": event_type,
            "event_level": level,
            "target_key": target_key,
            "stdout": message,
            "event_data": data or {},
            "occurred_at": datetime.now(timezone.utc),
        })

    def _insert(self, event, maximum_sequence):
        row = self.connection.execute(
            """
            WITH lock_run AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(hashtextextended(%(lock_key)s, 0))
            ), next_sequence AS (
                SELECT COALESCE(max(sequence_no), 0) + 1 AS value
                FROM automation.operation_event, lock_run
                WHERE run_id = %(run_id)s
            )
            INSERT INTO automation.operation_event (
                run_id, sequence_no, phase, event_type, event_level,
                target_key, play_name, task_name, task_action, changed,
                stdout, event_data, occurred_at
            )
            SELECT
                %(run_id)s, next_sequence.value, %(phase)s, %(event_type)s, %(level)s,
                %(target_key)s, %(play_name)s, %(task_name)s, %(task_action)s,
                %(changed)s, %(stdout)s, %(event_data)s,
                COALESCE(%(occurred_at)s::timestamptz, now())
            FROM next_sequence
            WHERE next_sequence.value <= %(maximum_sequence)s
            RETURNING sequence_no
            """,
            {
                "run_id": self.run_id,
                "lock_key": str(self.run_id),
                "maximum_sequence": maximum_sequence,
                "phase": event.get("phase") or "ansible",
                "event_type": event.get("event_type") or "unknown",
                "level": event.get("event_level") or "info",
                "target_key": redact_sensitive_text(event.get("target_key")),
                "play_name": redact_sensitive_text(event.get("play_name")),
                "task_name": redact_sensitive_text(event.get("task_name")),
                "task_action": redact_sensitive_text(event.get("task_action")),
                "changed": event.get("changed"),
                "stdout": _truncate_utf8(redact_sensitive_text(event.get("stdout"))),
                "event_data": Jsonb(redact_sensitive_data(event.get("event_data") or {})),
                "occurred_at": event.get("occurred_at"),
            },
        ).fetchone()
        return row["sequence_no"] if row else None

    def record(self, event):
        if self.failed or self.connection is None:
            return
        try:
            terminal = event.get("event_type") in TERMINAL_EVENT_TYPES
            sequence = self._insert(
                event,
                MAX_EVENTS_PER_RUN if terminal else MAX_STREAM_EVENTS_PER_RUN,
            )
            if sequence is None and not terminal:
                sequence = self._insert({
                    "phase": "ansible",
                    "event_type": "log_truncated",
                    "event_level": "warning",
                    "stdout": f"执行事件超过 {MAX_STREAM_EVENTS_PER_RUN} 条，后续过程日志已截断。",
                    "event_data": {"maximum_events": MAX_EVENTS_PER_RUN},
                    "occurred_at": datetime.now(timezone.utc),
                }, MAX_EVENTS_PER_RUN - 1)
            if sequence is None:
                return
            self.sequence = sequence
            if event.get("event_type") == "runner_on_start" and event.get("target_key"):
                self.connection.execute(
                    """
                    UPDATE automation.operation_host_result
                    SET status = 'running', started_at = COALESCE(started_at, now())
                    WHERE run_id = %(run_id)s AND target_key = %(target_key)s
                      AND status = 'queued'
                    """,
                    {"run_id": self.run_id, "target_key": event["target_key"]},
                )
        except Exception as exc:  # event persistence must not abort the automation itself
            self.failed = True
            LOGGER.exception("operation event persistence failed for %s: %s", self.run_id, exc)


def record_system_event(run_id, event_type, message, phase="dispatch", level="info", target_key=None, data=None, database=None):
    try:
        with OperationEventRecorder(run_id, database) as recorder:
            recorder.system(event_type, message, phase, level, target_key, data)
    except Exception as exc:  # task dispatch remains available if the timeline store is unavailable
        LOGGER.exception("system operation event persistence failed for %s: %s", run_id, exc)


def finalize_unhandled_run(run_id, error, database=None):
    """Closes a claimed task after an unexpected executor failure."""
    database = database or WriteDatabase()
    message = _truncate_utf8(redact_sensitive_text(error), 4000) or "未知执行异常"
    try:
        with database.connection() as connection:
            connection.execute(
                """
                UPDATE automation.operation_host_result
                SET status = 'failed', error_message = %(message)s,
                    started_at = COALESCE(started_at, now()), finished_at = now()
                WHERE run_id = %(run_id)s AND status IN ('queued', 'running')
                """,
                {"run_id": run_id, "message": f"任务执行异常：{message}"},
            )
            connection.execute(
                """
                UPDATE asset.server_connection connection
                SET bootstrap_status = 'failed', bootstrap_error = %(message)s,
                    bootstrap_completed_at = NULL, connection_status = 'unreachable',
                    last_error = %(message)s, last_checked_at = now()
                FROM automation.operation_host_result host
                JOIN automation.operation_run run ON run.run_id = host.run_id
                WHERE run.run_id = %(run_id)s
                  AND run.operation_code = 'server.bootstrap_ssh'
                  AND host.server_id = connection.server_id
                  AND connection.bootstrap_status IN ('pending', 'running')
                """,
                {"run_id": run_id, "message": f"任务执行异常：{message}"},
            )
            counts = connection.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
                       count(*) FILTER (WHERE status <> 'succeeded') AS failed
                FROM automation.operation_host_result WHERE run_id = %(run_id)s
                """,
                {"run_id": run_id},
            ).fetchone()
            succeeded = counts["succeeded"] or 0
            failed = counts["failed"] or 0
            final_status = "partial" if succeeded else "failed"
            updated = connection.execute(
                """
                UPDATE automation.operation_run
                SET status = %(status)s, succeeded_count = %(succeeded)s,
                    failed_count = %(failed)s, finished_at = now()
                WHERE run_id = %(run_id)s AND status IN ('queued', 'running')
                """,
                {
                    "run_id": run_id, "status": final_status,
                    "succeeded": succeeded, "failed": failed,
                },
            ).rowcount
        if updated:
            record_system_event(
                run_id, "task_failed", f"任务因执行器异常终止：{message}",
                phase="finalize", level="error",
                data={"status": final_status, "succeeded": succeeded, "failed": failed},
                database=database,
            )
    except Exception as exc:
        LOGGER.exception("failed to close operation run %s after executor error: %s", run_id, exc)
