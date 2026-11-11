import json
import ast
import unittest
from pathlib import Path

import httpx
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from agentswatch_mcp.api_client import AgentsWatchAPIClient
from agentswatch_mcp.auth import bearer_token_context, current_bearer_token
from agentswatch_mcp.server import create_application, create_mcp_server
from agentswatch_mcp.settings import MCPSettings


def _structured(result):
    value = getattr(result, "structuredContent", None)
    if value is not None:
        return value
    for content in result.content:
        text = getattr(content, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("tool result did not contain JSON")


class AgentsWatchMCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_client_discovers_core_and_generic_tools(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["authorization"], "Bearer aw_protocol")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "operations": [
                            {
                                "code": "custom.audit",
                                "name": "Custom Audit",
                                "scope": "server",
                                "risk_level": "low",
                                "parameters": {},
                            }
                        ]
                    }
                },
            )

        transport = httpx.MockTransport(handler)

        def client_factory():
            return AgentsWatchAPIClient(
                "http://api.test",
                current_bearer_token(),
                transport=transport,
            )

        protocol_server = create_mcp_server(client_factory=client_factory)
        with bearer_token_context("Bearer aw_protocol"):
            async with create_connected_server_and_client_session(
                protocol_server._mcp_server
            ) as session:
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                self.assertTrue(
                    {
                        "list_servers",
                        "create_runtime",
                        "retire_agent",
                        "list_groups",
                        "list_playbooks",
                        "run_playbook",
                        "get_task_events",
                        "publish_playbook",
                    }.issubset(names)
                )
                run_tool = next(
                    tool for tool in tools.tools if tool.name == "run_playbook"
                )
                self.assertIn("confirmation_id", run_tool.inputSchema["properties"])

                result = await session.call_tool("list_playbooks", {})
                structured = _structured(result)
                self.assertTrue(structured["ok"])
                self.assertEqual(
                    structured["result"]["data"]["operations"][0]["code"],
                    "custom.audit",
                )

    async def test_adapter_has_no_direct_database_ansible_or_shell_imports(self):
        package = Path(__file__).resolve().parents[1] / "agentswatch_mcp"
        forbidden = {
            "agentswatch_api",
            "agentswatch_automation",
            "ansible",
            "ansible_runner",
            "psycopg",
            "subprocess",
        }
        imports = set()
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module.split(".")[0])
        self.assertTrue(forbidden.isdisjoint(imports), forbidden & imports)


class AgentsWatchMCPStreamableHTTPTests(unittest.TestCase):
    RUNTIME_ID = "00000000-0000-4000-8000-000000000001"

    def setUp(self):
        self.requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path.endswith("/api/v1/automation/runs"):
                if request.headers.get("x-confirmation-id") != "cfm-1":
                    return httpx.Response(
                        409,
                        json={
                            "detail": {
                                "code": "CONFIRMATION_REQUIRED",
                                "message": "confirmation required",
                                "confirmation_id": "cfm-1",
                                "expires_at": "2026-07-16T12:00:00Z",
                            }
                        },
                    )
                return httpx.Response(
                    202, json={"data": {"run_id": "run-2", "status": "queued"}}
                )
            return httpx.Response(200, json={"data": {"server_id": "server-1"}})

        transport = httpx.MockTransport(handler)

        def client_factory():
            return AgentsWatchAPIClient(
                "http://api.test",
                current_bearer_token(),
                transport=transport,
            )

        settings = MCPSettings(api_base_url="http://api.test")
        self.app = create_application(
            settings=settings, client_factory=client_factory
        )

    @staticmethod
    def _headers():
        return {
            "Authorization": "Bearer aw_http_secret",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }

    @staticmethod
    def _initialize_payload():
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "agentswatch-test", "version": "1.0"},
            },
        }

    @staticmethod
    def _call_payload(request_id, name, arguments):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

    def test_health_is_public_and_mcp_requires_bearer(self):
        with TestClient(self.app, base_url="http://127.0.0.1:8010") as client:
            health = client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["service"], "agentswatch-mcp")

            unauthorized = client.post(
                "/mcp",
                json=self._initialize_payload(),
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
            self.assertEqual(unauthorized.status_code, 401)
            self.assertEqual(
                unauthorized.json()["error"]["code"], "AUTHENTICATION_REQUIRED"
            )

    def test_streamable_call_forwards_bearer_and_structured_confirmation(self):
        with TestClient(self.app, base_url="http://127.0.0.1:8010") as client:
            initialized = client.post(
                "/mcp", json=self._initialize_payload(), headers=self._headers()
            )
            self.assertEqual(initialized.status_code, 200, initialized.text)

            arguments = {
                "operation_code": "runtime.restart",
                "target_type": "runtime",
                "target_ids": [self.RUNTIME_ID],
                "parameters": {},
            }
            first = client.post(
                "/mcp",
                json=self._call_payload(2, "run_playbook", arguments),
                headers=self._headers(),
            )
            self.assertEqual(first.status_code, 200, first.text)
            first_result = first.json()["result"]["structuredContent"]
            self.assertFalse(first_result["ok"])
            self.assertEqual(
                first_result["error"]["code"], "CONFIRMATION_REQUIRED"
            )
            self.assertEqual(
                first_result["error"]["confirmation"]["confirmation_id"], "cfm-1"
            )

            arguments["confirmation_id"] = "cfm-1"
            second = client.post(
                "/mcp",
                json=self._call_payload(3, "run_playbook", arguments),
                headers=self._headers(),
            )
            self.assertEqual(second.status_code, 200, second.text)
            second_result = second.json()["result"]["structuredContent"]
            self.assertTrue(second_result["ok"])
            self.assertEqual(second_result["result"]["data"]["run_id"], "run-2")

        self.assertEqual(len(self.requests), 2)
        self.assertTrue(
            all(
                request.headers["authorization"] == "Bearer aw_http_secret"
                for request in self.requests
            )
        )
        self.assertNotIn("x-confirmation-id", self.requests[0].headers)
        self.assertEqual(self.requests[1].headers["x-confirmation-id"], "cfm-1")
        sent = json.loads(self.requests[1].content)
        self.assertNotIn("confirmation_id", sent)
        self.assertEqual(sent["runtime_ids"], [self.RUNTIME_ID])


if __name__ == "__main__":
    unittest.main()
