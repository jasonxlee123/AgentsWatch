"""Published automation discovery, execution, and task tools."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .common import ClientFactory, tool_result


def _validation_error(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "status_code": 422,
            "code": "VALIDATION_FAILED",
            "message": message,
        },
    }


def _operations_from_response(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    data = response.get("data")
    if not isinstance(data, dict):
        return []
    operations = data.get("operations")
    if not isinstance(operations, list):
        return []
    return [item for item in operations if isinstance(item, dict)]


def register_automation_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    """Register dynamic Playbook discovery and task lifecycle tools."""

    @mcp.tool()
    async def list_playbooks(
        scope: Literal["server", "runtime", "agent"] | None = None,
        risk_level: Literal["low", "medium", "high"] | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """Discover currently published operations from the AgentsWatch catalog.

        This list is dynamic: newly published Playbooks appear without an MCP code
        change or restart.
        """

        result = await tool_result(
            lambda: client_factory().get("/api/v1/automation/operations")
        )
        if not result["ok"]:
            return result

        operations = _operations_from_response(result["result"])
        query = (q or "").strip().casefold()
        filtered = []
        for operation in operations:
            if scope and operation.get("scope") != scope:
                continue
            if risk_level and operation.get("risk_level") != risk_level:
                continue
            if query:
                searchable = " ".join(
                    str(operation.get(key, ""))
                    for key in ("code", "name", "description", "category")
                ).casefold()
                if query not in searchable:
                    continue
            filtered.append(operation)
        return {
            "ok": True,
            "result": {"data": {"operations": filtered, "count": len(filtered)}},
        }

    @mcp.tool()
    async def get_playbook(operation_code: str) -> dict[str, Any]:
        """Get one currently published operation by its stable operation code."""

        result = await tool_result(
            lambda: client_factory().get("/api/v1/automation/operations")
        )
        if not result["ok"]:
            return result
        for operation in _operations_from_response(result["result"]):
            if operation.get("code") == operation_code:
                return {"ok": True, "result": {"data": operation}}
        return {
            "ok": False,
            "error": {
                "status_code": 404,
                "code": "NOT_FOUND",
                "message": f"published operation not found: {operation_code}",
            },
        }

    @mcp.tool()
    async def run_playbook(
        operation_code: str,
        target_type: Literal["server", "runtime", "agent"],
        target_ids: list[UUID] | None = None,
        group_id: UUID | None = None,
        parameters: dict[str, Any] | None = None,
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a task for any published Playbook operation.

        Supply either target_ids or, for server operations only, group_id. If the
        API requires confirmation, retry unchanged with the returned confirmation_id.
        """

        target_ids = target_ids or []
        if group_id and target_ids:
            return _validation_error("provide target_ids or group_id, not both")
        if group_id and target_type != "server":
            return _validation_error("group_id is supported only for server operations")
        if not group_id and not target_ids:
            return _validation_error("at least one target_id or group_id is required")
        if len(target_ids) != len(set(target_ids)):
            return _validation_error("target_ids must not contain duplicates")

        payload: dict[str, Any] = {
            "operation_code": operation_code,
            "parameters": parameters or {},
        }
        if group_id:
            payload["group_id"] = str(group_id)
        else:
            payload[f"{target_type}_ids"] = [str(target_id) for target_id in target_ids]
        return await tool_result(
            lambda: client_factory().post(
                "/api/v1/automation/runs",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def list_tasks(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        target_type: Literal["server", "runtime", "agent"] | None = None,
        operation_code: str | None = None,
        requested_by: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """List automation tasks and their status summary."""

        return await tool_result(
            lambda: client_factory().get(
                "/api/v1/automation/runs",
                params={
                    "limit": limit,
                    "offset": offset,
                    "status": status,
                    "target_type": target_type,
                    "operation_code": operation_code,
                    "requested_by": requested_by,
                    "q": q,
                },
            )
        )

    @mcp.tool()
    async def get_task(run_id: UUID) -> dict[str, Any]:
        """Get task status and per-target results."""

        return await tool_result(
            lambda: client_factory().get(f"/api/v1/automation/runs/{run_id}")
        )

    @mcp.tool()
    async def get_task_events(
        run_id: UUID,
        after_sequence: int = 0,
        limit: int = 200,
        target_key: str | None = None,
    ) -> dict[str, Any]:
        """Read ordered task events, optionally scoped to one target."""

        return await tool_result(
            lambda: client_factory().get(
                f"/api/v1/automation/runs/{run_id}/events",
                params={
                    "after_sequence": after_sequence,
                    "limit": limit,
                    "target_key": target_key,
                },
            )
        )

    @mcp.tool()
    async def cancel_task(
        run_id: UUID, confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Request cancellation of an eligible task."""

        return await tool_result(
            lambda: client_factory().post(
                f"/api/v1/automation/runs/{run_id}/cancel",
                json={},
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def retry_task(
        run_id: UUID, confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new task from an eligible failed or cancelled task."""

        return await tool_result(
            lambda: client_factory().post(
                f"/api/v1/automation/runs/{run_id}/retry",
                json={},
                confirmation_id=confirmation_id,
            )
        )
