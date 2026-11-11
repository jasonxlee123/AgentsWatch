"""Streamable HTTP MCP server backed only by the AgentsWatch REST API."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from . import __version__
from .api_client import AgentsWatchAPIClient
from .auth import BearerForwardingMiddleware, current_bearer_token
from .settings import MCPSettings
from .tools import (
    register_asset_tools,
    register_automation_tools,
    register_library_tools,
)
from .tools.common import ClientFactory


def _default_client_factory(settings: MCPSettings) -> ClientFactory:
    def build_client() -> AgentsWatchAPIClient:
        return AgentsWatchAPIClient(
            settings.api_base_url,
            current_bearer_token(),
            timeout=settings.request_timeout,
            verify=settings.tls_verify,
            uds=settings.api_uds,
        )

    return build_client


def create_mcp_server(
    *,
    settings: MCPSettings | None = None,
    client_factory: ClientFactory | None = None,
) -> FastMCP:
    """Build the protocol server without opening network sockets."""

    settings = settings or MCPSettings.from_environment()
    client_factory = client_factory or _default_client_factory(settings)
    server = FastMCP(
        "AgentsWatch",
        instructions=(
            "Manage AgentsWatch asset records, published Playbooks, and automation "
            "tasks through the AgentsWatch REST API. This server has no direct "
            "database, shell, or Ansible access. Dangerous calls may return a "
            "structured CONFIRMATION_REQUIRED result; retry the unchanged call with "
            "its confirmation_id."
        ),
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request):
        return JSONResponse(
            {
                "status": "ok",
                "service": "agentswatch-mcp",
                "version": __version__,
                "transport": "streamable-http",
            }
        )

    register_asset_tools(server, client_factory)
    register_automation_tools(server, client_factory)
    register_library_tools(server, client_factory)
    return server


def create_application(
    *,
    settings: MCPSettings | None = None,
    client_factory: ClientFactory | None = None,
):
    """Build the bearer-protected ASGI application."""

    protocol_server = create_mcp_server(
        settings=settings, client_factory=client_factory
    )
    return BearerForwardingMiddleware(
        protocol_server.streamable_http_app(), protected_path="/mcp"
    )


settings = MCPSettings.from_environment()
mcp = create_mcp_server(settings=settings)
app = BearerForwardingMiddleware(mcp.streamable_http_app(), protected_path="/mcp")
