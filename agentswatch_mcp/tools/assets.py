"""Server, runtime, agent, and management-group tools."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .common import ClientFactory, mutation_payload, tool_result


def register_asset_tools(mcp: FastMCP, client_factory: ClientFactory) -> None:
    """Register asset tools against a configured FastMCP instance."""

    @mcp.tool()
    async def list_servers(
        limit: int = 50,
        offset: int = 0,
        lifecycle_status: str | None = None,
        environment_code: str | None = None,
        group_code: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """List Servers visible to the current AgentsWatch user."""

        return await tool_result(
            lambda: client_factory().get(
                "/api/v1/servers",
                params={
                    "limit": limit,
                    "offset": offset,
                    "lifecycle_status": lifecycle_status,
                    "environment_code": environment_code,
                    "group_code": group_code,
                    "q": q,
                },
            )
        )

    @mcp.tool()
    async def get_server(server_id: UUID) -> dict[str, Any]:
        """Get one Server by UUID."""

        return await tool_result(
            lambda: client_factory().get(f"/api/v1/servers/{server_id}")
        )

    @mcp.tool()
    async def create_server(
        payload: dict[str, Any], confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Create a Server asset record; this does not install or configure a host."""

        return await tool_result(
            lambda: client_factory().post(
                "/api/v1/servers", json=payload, confirmation_id=confirmation_id
            )
        )

    @mcp.tool()
    async def update_server(
        server_id: UUID,
        row_version: int,
        changes: dict[str, Any],
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Update a Server asset using optimistic locking."""

        payload = mutation_payload(changes, row_version=row_version)
        return await tool_result(
            lambda: client_factory().patch(
                f"/api/v1/servers/{server_id}",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def retire_server(
        server_id: UUID, row_version: int, confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Retire a Server asset after any required server-side confirmation."""

        return await tool_result(
            lambda: client_factory().delete(
                f"/api/v1/servers/{server_id}",
                params={"row_version": row_version},
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def set_server_connection(
        server_id: UUID,
        connection: dict[str, Any],
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a Server connection contract through AgentsWatch REST."""

        return await tool_result(
            lambda: client_factory().put(
                f"/api/v1/servers/{server_id}/connection",
                json=connection,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def list_runtimes(
        limit: int = 50,
        offset: int = 0,
        lifecycle_status: str | None = None,
        family_code: str | None = None,
        deployment_mode: str | None = None,
        server_id: UUID | None = None,
        group_code: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """List Runtime asset records visible to the current user."""

        return await tool_result(
            lambda: client_factory().get(
                "/api/v1/runtimes",
                params={
                    "limit": limit,
                    "offset": offset,
                    "lifecycle_status": lifecycle_status,
                    "family_code": family_code,
                    "deployment_mode": deployment_mode,
                    "server_id": server_id,
                    "group_code": group_code,
                    "q": q,
                },
            )
        )

    @mcp.tool()
    async def get_runtime(runtime_id: UUID) -> dict[str, Any]:
        """Get one Runtime by UUID."""

        return await tool_result(
            lambda: client_factory().get(f"/api/v1/runtimes/{runtime_id}")
        )

    @mcp.tool()
    async def create_runtime(
        payload: dict[str, Any], confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Create a Runtime asset record without installing runtime software."""

        return await tool_result(
            lambda: client_factory().post(
                "/api/v1/runtimes", json=payload, confirmation_id=confirmation_id
            )
        )

    @mcp.tool()
    async def update_runtime(
        runtime_id: UUID,
        row_version: int,
        changes: dict[str, Any],
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Update a Runtime asset using optimistic locking."""

        payload = mutation_payload(changes, row_version=row_version)
        return await tool_result(
            lambda: client_factory().patch(
                f"/api/v1/runtimes/{runtime_id}",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def retire_runtime(
        runtime_id: UUID, row_version: int, confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Retire a Runtime asset after any required server-side confirmation."""

        return await tool_result(
            lambda: client_factory().delete(
                f"/api/v1/runtimes/{runtime_id}",
                params={"row_version": row_version},
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def list_agents(
        limit: int = 50,
        offset: int = 0,
        structure_role: str | None = None,
        service_archetype: str | None = None,
        lifecycle_status: str | None = None,
        department_code: str | None = None,
        group_code: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """List Agent asset records visible to the current user."""

        return await tool_result(
            lambda: client_factory().get(
                "/api/v1/agents",
                params={
                    "limit": limit,
                    "offset": offset,
                    "structure_role": structure_role,
                    "service_archetype": service_archetype,
                    "lifecycle_status": lifecycle_status,
                    "department_code": department_code,
                    "group_code": group_code,
                    "q": q,
                },
            )
        )

    @mcp.tool()
    async def get_agent(agent_id: UUID) -> dict[str, Any]:
        """Get one Agent by UUID."""

        return await tool_result(
            lambda: client_factory().get(f"/api/v1/agents/{agent_id}")
        )

    @mcp.tool()
    async def create_agent(
        payload: dict[str, Any], confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Create an Agent asset record without deploying an external agent."""

        return await tool_result(
            lambda: client_factory().post(
                "/api/v1/agents", json=payload, confirmation_id=confirmation_id
            )
        )

    @mcp.tool()
    async def update_agent(
        agent_id: UUID,
        row_version: int,
        changes: dict[str, Any],
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Update an Agent asset using optimistic locking."""

        payload = mutation_payload(changes, row_version=row_version)
        return await tool_result(
            lambda: client_factory().patch(
                f"/api/v1/agents/{agent_id}",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def retire_agent(
        agent_id: UUID, row_version: int, confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Retire an Agent asset after any required server-side confirmation."""

        return await tool_result(
            lambda: client_factory().delete(
                f"/api/v1/agents/{agent_id}",
                params={"row_version": row_version},
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def list_groups(
        limit: int = 50,
        offset: int = 0,
        resource_type: str | None = None,
        lifecycle_status: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """List management Groups visible to the current user."""

        return await tool_result(
            lambda: client_factory().get(
                "/api/v1/groups",
                params={
                    "limit": limit,
                    "offset": offset,
                    "resource_type": resource_type,
                    "lifecycle_status": lifecycle_status,
                    "q": q,
                },
            )
        )

    @mcp.tool()
    async def get_group(group_id: UUID) -> dict[str, Any]:
        """Get one management Group by UUID."""

        return await tool_result(
            lambda: client_factory().get(f"/api/v1/groups/{group_id}")
        )

    @mcp.tool()
    async def create_group(
        payload: dict[str, Any], confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Create a management Group asset record."""

        return await tool_result(
            lambda: client_factory().post(
                "/api/v1/groups", json=payload, confirmation_id=confirmation_id
            )
        )

    @mcp.tool()
    async def update_group(
        group_id: UUID,
        row_version: int,
        changes: dict[str, Any],
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Update a management Group using optimistic locking."""

        payload = mutation_payload(changes, row_version=row_version)
        return await tool_result(
            lambda: client_factory().patch(
                f"/api/v1/groups/{group_id}",
                json=payload,
                confirmation_id=confirmation_id,
            )
        )

    @mcp.tool()
    async def retire_group(
        group_id: UUID, row_version: int, confirmation_id: str | None = None
    ) -> dict[str, Any]:
        """Retire a management Group after any required server-side confirmation."""

        return await tool_result(
            lambda: client_factory().delete(
                f"/api/v1/groups/{group_id}",
                params={"row_version": row_version},
                confirmation_id=confirmation_id,
            )
        )
