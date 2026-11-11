"""Automation-library draft, validation, and publication tools."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .common import ClientFactory, mutation_payload, tool_result


def register_library_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    """Register admin-only library tools; AgentsWatch enforces authorization."""

    @mcp.tool()
    async def list_playbook_definitions(
        limit: int = 50,
        offset: int = 0,
        scope: Literal["server", "runtime", "agent"] | None = None,
        lifecycle_status: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """List managed Playbook definitions, including drafts."""

        return await tool_result(
            lambda: client_factory().get(
                "/api/v1/automation-library",
                params={
                    "limit": limit,
                    "offset": offset,
                    "scope": scope,
                    "lifecycle_status": lifecycle_status,
                    "q": q,
                },
            )
        )

    @mcp.tool()
    async def get_playbook_definition(automation_id: UUID) -> dict[str, Any]:
        """Get one Playbook definition with version details."""

        return await tool_result(
            lambda: client_factory().get(
                f"/api/v1/automation-library/{automation_id}"
            )
        )

    @mcp.tool()
    async def create_playbook_draft(
        payload: dict[str, Any], confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Create a Playbook definition and its initial draft version."""

        return await tool_result(
            lambda: client_factory().post(
                "/api/v1/automation-library",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def create_playbook_version(
        automation_id: UUID,
        version_label: str,
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new draft version for an existing Playbook definition."""

        return await tool_result(
            lambda: client_factory().post(
                f"/api/v1/automation-library/{automation_id}/versions",
                json={"version_label": version_label},
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def update_playbook_draft(
        automation_id: UUID,
        version_id: UUID,
        row_version: int,
        version_row_version: int,
        changes: dict[str, Any],
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Update an editable Playbook draft using optimistic locking."""

        payload = mutation_payload(
            changes,
            row_version=row_version,
            version_row_version=version_row_version,
        )
        return await tool_result(
            lambda: client_factory().patch(
                f"/api/v1/automation-library/{automation_id}/versions/{version_id}",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def validate_playbook(
        automation_id: UUID,
        version_id: UUID,
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate a Playbook draft through the AgentsWatch library service."""

        return await tool_result(
            lambda: client_factory().post(
                f"/api/v1/automation-library/{automation_id}/versions/{version_id}/validate",
                json={},
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def publish_playbook(
        automation_id: UUID,
        version_id: UUID,
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Publish a validated Playbook after server-side confirmation if required."""

        return await tool_result(
            lambda: client_factory().post(
                f"/api/v1/automation-library/{automation_id}/versions/{version_id}/publish",
                json={},
                confirmation_id=confirmation_id,
            )
        )
