import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from agentswatch_automation.catalog import OperationCatalog, OperationDefinition
from agentswatch_automation.paths import (
    CODE_AUTOMATION_ROOT,
    managed_root,
    resolve_managed_playbook,
)

from .database import Database, WriteDatabase
from .server_management import ConflictError, NotFoundError


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_ROOT = CODE_AUTOMATION_ROOT
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,99}$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,49}$")
MAX_PLAYBOOK_BYTES = 256 * 1024


def _content_hash(playbook_content: str, source_paths: list[str] | tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    digest.update(playbook_content.encode("utf-8"))
    for value in source_paths:
        path = (AUTOMATION_ROOT / value).resolve()
        if AUTOMATION_ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"automation source does not exist or is outside automation root: {value}")
        digest.update(value.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _validate_parameter_schema(parameters: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(parameters or {})
    for name, definition in values.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("parameter names cannot be empty")
        if not isinstance(definition, dict):
            raise ValueError(f"parameter definition must be an object: {name}")
        parameter_type = definition.get("type")
        if parameter_type not in {"string", "integer", "number", "boolean", "array"}:
            raise ValueError(f"unsupported parameter type: {name}")
        if parameter_type == "array" and definition.get("items") not in {
            "string", "integer", "number", "boolean",
        }:
            raise ValueError(f"unsupported array item type: {name}")
        if "enum" in definition and not isinstance(definition["enum"], list):
            raise ValueError(f"parameter enum must be an array: {name}")
    return values


def _validate_definition(values: dict[str, Any]):
    code = str(values.get("code") or "").strip()
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError("automation code must use lowercase letters, numbers, dot, underscore or hyphen")
    if values.get("scope") not in {"server", "runtime", "agent"}:
        raise ValueError("automation scope must be server, runtime or agent")
    if values.get("risk_level") not in {"low", "medium", "high"}:
        raise ValueError("automation risk level must be low, medium or high")
    timeout = int(values.get("timeout_seconds") or 0)
    if timeout < 1 or timeout > 3600:
        raise ValueError("automation timeout must be between 1 and 3600 seconds")
    for name in ("name", "category"):
        if not str(values.get(name) or "").strip():
            raise ValueError(f"automation {name} cannot be empty")


def _validate_version_label(value: str) -> str:
    label = str(value or "").strip()
    if not VERSION_PATTERN.fullmatch(label):
        raise ValueError("version label contains unsupported characters")
    return label


def _validate_playbook_content(value: str) -> str:
    content = str(value or "")
    if not content.strip():
        raise ValueError("playbook content cannot be empty")
    if len(content.encode("utf-8")) > MAX_PLAYBOOK_BYTES:
        raise ValueError("playbook content exceeds 256 KiB")
    return content


class AutomationLibraryService:
    def __init__(self, database=None, read_database=None, builtin_catalog=None):
        self.database = database or WriteDatabase()
        self.read_database = read_database or Database()
        self.builtin_catalog = builtin_catalog or OperationCatalog()

    def sync_builtin_catalog(self, actor="system:catalog-sync"):
        with self.database.connection() as connection:
            for operation in self.builtin_catalog.list():
                playbook_path = str(operation.playbook.relative_to(AUTOMATION_ROOT))
                source_paths = [str(path.relative_to(AUTOMATION_ROOT)) for path in operation.source_paths]
                playbook_content = operation.playbook.read_text(encoding="utf-8")
                content_hash = _content_hash(playbook_content, source_paths)
                row = connection.execute(
                    """
                    INSERT INTO automation.automation_definition (
                        code, name, description, category, scope, lifecycle_status,
                        risk_level, requires_become, supports_batch, timeout_seconds,
                        builtin, created_by, updated_by
                    ) VALUES (
                        %(code)s, %(name)s, %(description)s, %(category)s, %(scope)s, 'published',
                        %(risk_level)s, %(requires_become)s, %(supports_batch)s, %(timeout_seconds)s,
                        true, %(actor)s, %(actor)s
                    )
                    ON CONFLICT (code) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        category = EXCLUDED.category,
                        scope = EXCLUDED.scope,
                        risk_level = EXCLUDED.risk_level,
                        requires_become = EXCLUDED.requires_become,
                        supports_batch = EXCLUDED.supports_batch,
                        timeout_seconds = EXCLUDED.timeout_seconds,
                        updated_by = EXCLUDED.updated_by
                    WHERE automation.automation_definition.builtin
                      AND (
                        automation.automation_definition.name,
                        automation.automation_definition.description,
                        automation.automation_definition.category,
                        automation.automation_definition.scope,
                        automation.automation_definition.risk_level,
                        automation.automation_definition.requires_become,
                        automation.automation_definition.supports_batch,
                        automation.automation_definition.timeout_seconds
                      ) IS DISTINCT FROM (
                        EXCLUDED.name, EXCLUDED.description, EXCLUDED.category, EXCLUDED.scope,
                        EXCLUDED.risk_level, EXCLUDED.requires_become,
                        EXCLUDED.supports_batch, EXCLUDED.timeout_seconds
                      )
                    RETURNING automation_id, current_published_version_id, builtin
                    """,
                    {
                        "code": operation.code,
                        "name": operation.name,
                        "description": operation.description,
                        "category": operation.category,
                        "scope": operation.scope,
                        "risk_level": operation.risk_level,
                        "requires_become": operation.requires_become,
                        "supports_batch": operation.supports_batch,
                        "timeout_seconds": operation.timeout_seconds,
                        "actor": actor,
                    },
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT automation_id, current_published_version_id, builtin
                        FROM automation.automation_definition WHERE code = %(code)s
                        """,
                        {"code": operation.code},
                    ).fetchone()
                if not row["builtin"]:
                    raise ConflictError(f"managed automation conflicts with builtin code: {operation.code}")
                version = connection.execute(
                    """
                    SELECT automation_version_id, content_hash, source_kind
                    FROM automation.automation_version
                    WHERE automation_id = %(automation_id)s AND version_label = %(version_label)s
                    """,
                    {"automation_id": row["automation_id"], "version_label": operation.version},
                ).fetchone()
                if version and version["content_hash"].strip() != content_hash:
                    raise ConflictError(
                        f"builtin automation {operation.code} changed without a version bump"
                    )
                if version is None:
                    version_id = uuid4()
                    connection.execute(
                        """
                        INSERT INTO automation.automation_version (
                            automation_version_id, automation_id, version_label, version_status,
                            source_kind, playbook_path, playbook_content, source_paths,
                            parameter_schema, content_hash, validation_status, validation_report,
                            validated_at, created_by, published_by, published_at
                        ) VALUES (
                            %(version_id)s, %(automation_id)s, %(version_label)s, 'published',
                            'builtin', %(playbook_path)s, %(playbook_content)s, %(source_paths)s,
                            %(parameters)s, %(content_hash)s, 'passed', %(validation_report)s,
                            now(), %(actor)s, %(actor)s, now()
                        )
                        """,
                        {
                            "version_id": version_id,
                            "automation_id": row["automation_id"],
                            "version_label": operation.version,
                            "playbook_path": playbook_path,
                            "playbook_content": playbook_content,
                            "source_paths": Jsonb(source_paths),
                            "parameters": Jsonb(operation.parameters),
                            "content_hash": content_hash,
                            "validation_report": Jsonb({"source": "builtin-catalog", "content_hash": content_hash}),
                            "actor": actor,
                        },
                    )
                    version = {"automation_version_id": version_id, "source_kind": "builtin"}
                current = None
                if row["current_published_version_id"]:
                    current = connection.execute(
                        """
                        SELECT source_kind FROM automation.automation_version
                        WHERE automation_version_id = %(version_id)s
                        """,
                        {"version_id": row["current_published_version_id"]},
                    ).fetchone()
                if row["current_published_version_id"] is None or current["source_kind"] == "builtin":
                    if row["current_published_version_id"] != version["automation_version_id"]:
                        connection.execute(
                            """
                            UPDATE automation.automation_definition
                            SET current_published_version_id = %(version_id)s,
                                lifecycle_status = 'published', updated_by = %(actor)s
                            WHERE automation_id = %(automation_id)s
                            """,
                            {
                                "version_id": version["automation_version_id"],
                                "actor": actor,
                                "automation_id": row["automation_id"],
                            },
                        )

    def list(self, limit=100, offset=0, scope=None, lifecycle_status=None, q=None):
        clauses = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if scope:
            clauses.append("definition.scope = %(scope)s")
            params["scope"] = scope
        if lifecycle_status:
            clauses.append("definition.lifecycle_status = %(lifecycle_status)s")
            params["lifecycle_status"] = lifecycle_status
        if q:
            clauses.append(
                "(definition.code ILIKE %(q)s OR definition.name ILIKE %(q)s OR definition.description ILIKE %(q)s)"
            )
            params["q"] = f"%{q}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.read_database.connection() as connection:
            total = connection.execute(
                f"SELECT count(*) AS total FROM automation.automation_definition definition {where}", params
            ).fetchone()["total"]
            rows = connection.execute(
                f"""
                SELECT definition.automation_id, definition.code, definition.name,
                       definition.description, definition.category, definition.scope,
                       definition.lifecycle_status, definition.risk_level,
                       definition.requires_become, definition.supports_batch,
                       definition.timeout_seconds, definition.builtin,
                       definition.row_version, definition.updated_at,
                       version.automation_version_id, version.version_label,
                       version.content_hash, version.validation_status,
                       (SELECT count(*) FROM automation.automation_version history
                        WHERE history.automation_id = definition.automation_id) AS version_count,
                       EXISTS (
                           SELECT 1 FROM automation.automation_version draft
                           WHERE draft.automation_id = definition.automation_id
                             AND draft.version_status = 'draft'
                       ) AS has_draft,
                       (SELECT count(*) FROM automation.operation_run run
                        WHERE run.operation_code = definition.code) AS run_count,
                       (SELECT max(run.created_at) FROM automation.operation_run run
                        WHERE run.operation_code = definition.code) AS last_run_at
                FROM automation.automation_definition definition
                LEFT JOIN automation.automation_version version
                  ON version.automation_version_id = definition.current_published_version_id
                {where}
                ORDER BY definition.category, definition.name, definition.code
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            ).fetchall()
        return {"data": list(rows), "page": {"limit": limit, "offset": offset, "total": total}}

    def get(self, automation_id: UUID):
        with self.read_database.connection() as connection:
            item = connection.execute(
                """
                SELECT automation_id, code, name, description, category, scope,
                       lifecycle_status, risk_level, requires_become, supports_batch,
                       timeout_seconds, builtin, current_published_version_id,
                       row_version, created_by, updated_by, created_at, updated_at
                FROM automation.automation_definition
                WHERE automation_id = %(automation_id)s
                """,
                {"automation_id": automation_id},
            ).fetchone()
            if item is None:
                return None
            item["versions"] = list(connection.execute(
                """
                SELECT automation_version_id, version_label, version_status, source_kind,
                       playbook_path, playbook_content, source_paths, parameter_schema,
                       content_hash, validation_status, validation_report, validated_at,
                       created_by, published_by, published_at, row_version,
                       created_at, updated_at
                FROM automation.automation_version
                WHERE automation_id = %(automation_id)s
                ORDER BY created_at DESC, automation_version_id DESC
                """,
                {"automation_id": automation_id},
            ).fetchall())
            item["current_version"] = next(
                (
                    version for version in item["versions"]
                    if version["automation_version_id"] == item["current_published_version_id"]
                ),
                None,
            )
            item["draft_version"] = next(
                (version for version in item["versions"] if version["version_status"] == "draft"), None
            )
            item["run_summary"] = connection.execute(
                """
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
                       count(*) FILTER (WHERE status IN ('failed', 'partial')) AS failed,
                       count(*) FILTER (WHERE status IN ('queued', 'running')) AS active,
                       max(created_at) AS last_run_at
                FROM automation.operation_run
                WHERE operation_code = %(operation_code)s
                """,
                {"operation_code": item["code"]},
            ).fetchone()
            item["recent_runs"] = list(connection.execute(
                """
                SELECT run_id, status::text AS status, target_type, target_count,
                       succeeded_count, failed_count, requested_by, playbook_version,
                       automation_version_id, retry_of_run_id,
                       created_at, started_at, finished_at
                FROM automation.operation_run
                WHERE operation_code = %(operation_code)s
                ORDER BY created_at DESC, run_id DESC
                LIMIT 10
                """,
                {"operation_code": item["code"]},
            ).fetchall())
            return item

    def create(self, values: dict[str, Any], actor="local-user"):
        normalized = {
            "code": str(values.get("code") or "").strip(),
            "name": str(values.get("name") or "").strip(),
            "description": str(values.get("description") or "").strip(),
            "category": str(values.get("category") or "未分类").strip(),
            "scope": values.get("scope"),
            "risk_level": values.get("risk_level", "low"),
            "requires_become": bool(values.get("requires_become", False)),
            "supports_batch": bool(values.get("supports_batch", False)),
            "timeout_seconds": int(values.get("timeout_seconds", 120)),
        }
        _validate_definition(normalized)
        version_label = _validate_version_label(values.get("version_label", "1.0.0"))
        content = _validate_playbook_content(values.get("playbook_content", ""))
        parameters = _validate_parameter_schema(values.get("parameter_schema"))
        content_hash = _content_hash(content)
        automation_id = uuid4()
        version_id = uuid4()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO automation.automation_definition (
                    automation_id, code, name, description, category, scope,
                    lifecycle_status, risk_level, requires_become, supports_batch,
                    timeout_seconds, builtin, created_by, updated_by
                ) VALUES (
                    %(automation_id)s, %(code)s, %(name)s, %(description)s, %(category)s,
                    %(scope)s, 'draft', %(risk_level)s, %(requires_become)s,
                    %(supports_batch)s, %(timeout_seconds)s, false, %(actor)s, %(actor)s
                )
                """,
                {**normalized, "automation_id": automation_id, "actor": actor},
            )
            connection.execute(
                """
                INSERT INTO automation.automation_version (
                    automation_version_id, automation_id, version_label, version_status,
                    source_kind, playbook_content, parameter_schema, content_hash, created_by
                ) VALUES (
                    %(version_id)s, %(automation_id)s, %(version_label)s, 'draft',
                    'managed', %(content)s, %(parameters)s, %(content_hash)s, %(actor)s
                )
                """,
                {
                    "version_id": version_id, "automation_id": automation_id,
                    "version_label": version_label, "content": content,
                    "parameters": Jsonb(parameters), "content_hash": content_hash, "actor": actor,
                },
            )
        return automation_id

    def create_draft_version(self, automation_id: UUID, version_label: str, actor="local-user"):
        label = _validate_version_label(version_label)
        with self.database.connection() as connection:
            definition = connection.execute(
                """
                SELECT current_published_version_id FROM automation.automation_definition
                WHERE automation_id = %(automation_id)s FOR UPDATE
                """,
                {"automation_id": automation_id},
            ).fetchone()
            if definition is None:
                raise NotFoundError("automation definition not found")
            if definition["current_published_version_id"] is None:
                raise ConflictError("automation has no published version to copy")
            current = connection.execute(
                """
                SELECT playbook_content, source_paths, parameter_schema, content_hash
                FROM automation.automation_version
                WHERE automation_version_id = %(version_id)s
                """,
                {"version_id": definition["current_published_version_id"]},
            ).fetchone()
            version_id = uuid4()
            connection.execute(
                """
                INSERT INTO automation.automation_version (
                    automation_version_id, automation_id, version_label, version_status,
                    source_kind, playbook_content, source_paths, parameter_schema,
                    content_hash, validation_status, created_by
                ) VALUES (
                    %(version_id)s, %(automation_id)s, %(version_label)s, 'draft',
                    'managed', %(content)s, %(source_paths)s, %(parameters)s,
                    %(content_hash)s, 'pending', %(actor)s
                )
                """,
                {
                    "version_id": version_id, "automation_id": automation_id,
                    "version_label": label, "content": current["playbook_content"],
                    "source_paths": Jsonb(current["source_paths"]),
                    "parameters": Jsonb(current["parameter_schema"]),
                    "content_hash": current["content_hash"].strip(), "actor": actor,
                },
            )
        return version_id

    def update_draft(self, automation_id: UUID, version_id: UUID, values: dict[str, Any],
                     expected_definition_version: int, expected_version_version: int, actor="local-user"):
        with self.database.connection() as connection:
            definition = connection.execute(
                """
                SELECT * FROM automation.automation_definition
                WHERE automation_id = %(automation_id)s FOR UPDATE
                """,
                {"automation_id": automation_id},
            ).fetchone()
            version = connection.execute(
                """
                SELECT * FROM automation.automation_version
                WHERE automation_version_id = %(version_id)s
                  AND automation_id = %(automation_id)s FOR UPDATE
                """,
                {"automation_id": automation_id, "version_id": version_id},
            ).fetchone()
            if definition is None or version is None:
                raise NotFoundError("automation draft not found")
            if version["version_status"] != "draft":
                raise ConflictError("published automation versions are immutable")
            if definition["row_version"] != expected_definition_version or version["row_version"] != expected_version_version:
                raise ConflictError("automation draft has changed; reload and retry")
            metadata = {
                "name": str(values.get("name", definition["name"])).strip(),
                "description": str(values.get("description", definition["description"])).strip(),
                "category": str(values.get("category", definition["category"])).strip(),
                "scope": values.get("scope", definition["scope"]),
                "risk_level": values.get("risk_level", definition["risk_level"]),
                "requires_become": bool(values.get("requires_become", definition["requires_become"])),
                "supports_batch": bool(values.get("supports_batch", definition["supports_batch"])),
                "timeout_seconds": int(values.get("timeout_seconds", definition["timeout_seconds"])),
                "code": definition["code"],
            }
            _validate_definition(metadata)
            label = _validate_version_label(values.get("version_label", version["version_label"]))
            content = _validate_playbook_content(values.get("playbook_content", version["playbook_content"]))
            parameters = _validate_parameter_schema(values.get("parameter_schema", version["parameter_schema"]))
            content_hash = _content_hash(content, list(version["source_paths"]))
            connection.execute(
                """
                UPDATE automation.automation_definition
                SET name = %(name)s, description = %(description)s, category = %(category)s,
                    scope = %(scope)s, risk_level = %(risk_level)s,
                    requires_become = %(requires_become)s, supports_batch = %(supports_batch)s,
                    timeout_seconds = %(timeout_seconds)s, updated_by = %(actor)s
                WHERE automation_id = %(automation_id)s
                """,
                {**metadata, "actor": actor, "automation_id": automation_id},
            )
            connection.execute(
                """
                UPDATE automation.automation_version
                SET version_label = %(version_label)s, playbook_content = %(content)s,
                    parameter_schema = %(parameters)s, content_hash = %(content_hash)s,
                    validation_status = 'pending', validation_report = NULL, validated_at = NULL
                WHERE automation_version_id = %(version_id)s
                """,
                {
                    "version_label": label, "content": content, "parameters": Jsonb(parameters),
                    "content_hash": content_hash, "version_id": version_id,
                },
            )

    def validate_version(self, automation_id: UUID, version_id: UUID):
        with self.database.connection() as connection:
            version = connection.execute(
                """
                SELECT version_status, playbook_content, source_paths, content_hash
                FROM automation.automation_version
                WHERE automation_id = %(automation_id)s
                  AND automation_version_id = %(version_id)s
                """,
                {"automation_id": automation_id, "version_id": version_id},
            ).fetchone()
        if version is None:
            raise NotFoundError("automation version not found")
        if version["version_status"] != "draft":
            raise ConflictError("only draft automation versions can be validated")
        virtualenv_executable = Path(sys.executable).absolute().parent / "ansible-playbook"
        executable = str(virtualenv_executable) if virtualenv_executable.is_file() else shutil.which("ansible-playbook")
        if not executable:
            raise RuntimeError("ansible-playbook is not installed")
        validation_root = managed_root()
        validation_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="validate-", suffix=".yml", dir=validation_root)
        temp_path = Path(temp_name)
        try:
            os.close(fd)
            temp_path.write_text(version["playbook_content"], encoding="utf-8")
            process = subprocess.run(
                [executable, "--syntax-check", "-i", "localhost,", str(temp_path)],
                cwd=AUTOMATION_ROOT,
                env={**os.environ, "ANSIBLE_CONFIG": str(AUTOMATION_ROOT / "ansible.cfg")},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            passed = process.returncode == 0
            report = {
                "content_hash": version["content_hash"].strip(),
                "return_code": process.returncode,
                "stdout": process.stdout[-8000:],
                "stderr": process.stderr[-8000:],
            }
        except subprocess.TimeoutExpired as exc:
            passed = False
            report = {
                "content_hash": version["content_hash"].strip(),
                "return_code": None,
                "stdout": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
                "stderr": "Ansible syntax validation timed out",
            }
        finally:
            temp_path.unlink(missing_ok=True)
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE automation.automation_version
                SET validation_status = %(status)s, validation_report = %(report)s,
                    validated_at = now()
                WHERE automation_version_id = %(version_id)s
                  AND content_hash = %(content_hash)s
                """,
                {
                    "status": "passed" if passed else "failed", "report": Jsonb(report),
                    "version_id": version_id, "content_hash": version["content_hash"],
                },
            )
        return {"validation_status": "passed" if passed else "failed", **report}

    @staticmethod
    def _materialize(automation_id: UUID, version_id: UUID, content_hash: str, content: str) -> str:
        relative = Path("managed") / str(automation_id) / f"{version_id}-{content_hash[:12]}.yml"
        target = resolve_managed_playbook(relative)
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}-", suffix=".tmp", dir=target.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            temp.chmod(0o600)
            os.replace(temp, target)
            target.chmod(0o600)
        finally:
            temp.unlink(missing_ok=True)
        return str(relative)

    def publish_version(self, automation_id: UUID, version_id: UUID, actor="local-user"):
        with self.database.connection() as connection:
            version = connection.execute(
                """
                SELECT version_status, playbook_content, content_hash,
                       validation_status, validation_report
                FROM automation.automation_version
                WHERE automation_id = %(automation_id)s
                  AND automation_version_id = %(version_id)s FOR UPDATE
                """,
                {"automation_id": automation_id, "version_id": version_id},
            ).fetchone()
            if version is None:
                raise NotFoundError("automation version not found")
            if version["version_status"] != "draft":
                raise ConflictError("only draft automation versions can be published")
            report = version["validation_report"] or {}
            if version["validation_status"] != "passed" or report.get("content_hash") != version["content_hash"].strip():
                raise ConflictError("automation draft must pass validation after its latest edit")
            playbook_path = self._materialize(
                automation_id, version_id, version["content_hash"].strip(), version["playbook_content"]
            )
            connection.execute(
                """
                UPDATE automation.automation_version
                SET version_status = 'published', playbook_path = %(playbook_path)s,
                    published_by = %(actor)s, published_at = now()
                WHERE automation_version_id = %(version_id)s
                """,
                {"playbook_path": playbook_path, "actor": actor, "version_id": version_id},
            )
            result = connection.execute(
                """
                UPDATE automation.automation_definition
                SET current_published_version_id = %(version_id)s,
                    lifecycle_status = 'published', updated_by = %(actor)s
                WHERE automation_id = %(automation_id)s
                RETURNING code
                """,
                {"version_id": version_id, "actor": actor, "automation_id": automation_id},
            ).fetchone()
            if result is None:
                raise NotFoundError("automation definition not found")
        return result["code"]


class ManagedOperationCatalog(OperationCatalog):
    def __init__(self, database=None):
        super().__init__()
        read_database = database or Database()
        try:
            with read_database.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT definition.automation_id, definition.code, definition.name, definition.description,
                           definition.category, definition.scope, definition.risk_level,
                           definition.requires_become, definition.supports_batch,
                           definition.timeout_seconds, version.automation_version_id,
                           version.version_label, version.source_kind, version.playbook_path,
                           version.playbook_content, version.source_paths,
                           version.parameter_schema, version.content_hash
                    FROM automation.automation_definition definition
                    JOIN automation.automation_version version
                      ON version.automation_version_id = definition.current_published_version_id
                    WHERE definition.lifecycle_status = 'published'
                      AND version.version_status = 'published'
                    """
                ).fetchall()
        except Exception:
            return
        for row in rows:
            relative = Path(row["playbook_path"] or "")
            if row["source_kind"] == "managed":
                playbook = resolve_managed_playbook(relative)
            else:
                if relative.is_absolute():
                    raise ValueError(f"builtin playbook is outside automation root: {row['code']}")
                playbook = (AUTOMATION_ROOT / relative).resolve()
                if AUTOMATION_ROOT.resolve() not in playbook.parents:
                    raise ValueError(f"builtin playbook is outside automation root: {row['code']}")
            if not playbook.is_file():
                if row["source_kind"] != "managed":
                    raise ValueError(f"builtin playbook is missing: {row['code']}")
                relative = AutomationLibraryService._materialize(
                    UUID(str(row["automation_id"])),
                    UUID(str(row["automation_version_id"])),
                    row["content_hash"].strip(),
                    row["playbook_content"],
                )
                playbook = resolve_managed_playbook(relative)
            source_paths = tuple((AUTOMATION_ROOT / value).resolve() for value in row["source_paths"])
            for source_path in source_paths:
                if AUTOMATION_ROOT.resolve() not in source_path.parents or not source_path.is_file():
                    raise ValueError(f"managed automation source is invalid: {row['code']}")
            self._operations[row["code"]] = OperationDefinition(
                code=row["code"], name=row["name"], description=row["description"],
                category=row["category"], scope=row["scope"], version=row["version_label"],
                playbook=playbook, source_paths=source_paths, risk_level=row["risk_level"],
                requires_become=row["requires_become"], supports_batch=row["supports_batch"],
                timeout_seconds=row["timeout_seconds"], parameters=row["parameter_schema"],
                version_id=str(row["automation_version_id"]),
            )
