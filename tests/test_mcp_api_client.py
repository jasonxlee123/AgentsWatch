import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agentswatch_mcp.api_client import AgentsWatchAPIClient
from agentswatch_mcp.errors import AgentsWatchAPIError, ConfirmationRequiredError
from agentswatch_mcp.settings import MCPSettings


class AgentsWatchMCPAPIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_bearer_and_confirmation_without_mutating_payload(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(202, json={"data": {"run_id": "run-1"}})

        client = AgentsWatchAPIClient(
            "https://agentswatch.example/management",
            "Bearer aw_key_secret",
            transport=httpx.MockTransport(handler),
        )
        payload = {
            "operation_code": "runtime.restart",
            "runtime_ids": ["runtime-1"],
            "parameters": {},
        }

        result = await client.post(
            "/api/v1/automation/runs",
            json=payload,
            confirmation_id="cfm-1",
        )

        self.assertEqual(result, {"data": {"run_id": "run-1"}})
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(
            str(request.url),
            "https://agentswatch.example/management/api/v1/automation/runs",
        )
        self.assertEqual(request.headers["authorization"], "Bearer aw_key_secret")
        self.assertEqual(request.headers["x-confirmation-id"], "cfm-1")
        self.assertEqual(json.loads(request.content), payload)

    async def test_confirmation_required_is_preserved_structurally(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "CONFIRMATION_REQUIRED",
                        "message": "请确认本次操作",
                        "confirmation": {
                            "confirmation_id": "cfm-123",
                            "expires_at": "2026-07-16T12:00:00Z",
                        },
                    }
                },
            )

        client = AgentsWatchAPIClient(
            "http://api.test",
            "Bearer aw_test",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(ConfirmationRequiredError) as raised:
            await client.delete(
                "/api/v1/servers/server-1", params={"row_version": 1}
            )

        result = raised.exception.as_tool_result()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "CONFIRMATION_REQUIRED")
        self.assertEqual(
            result["error"]["confirmation"]["confirmation_id"], "cfm-123"
        )
        self.assertEqual(
            result["error"]["confirmation"]["expires_at"],
            "2026-07-16T12:00:00Z",
        )

    async def test_plain_fastapi_errors_receive_stable_codes(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "权限不足"})

        client = AgentsWatchAPIClient(
            "http://api.test",
            "Bearer aw_test",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(AgentsWatchAPIError) as raised:
            await client.get("/api/v1/servers")

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.code, "PERMISSION_DENIED")
        self.assertEqual(raised.exception.message, "权限不足")

    async def test_rejects_non_rest_paths(self):
        client = AgentsWatchAPIClient("http://api.test", "Bearer aw_test")

        with self.assertRaisesRegex(ValueError, "/api/v1/"):
            await client.get("/healthz")
        with self.assertRaisesRegex(ValueError, "/api/v1/"):
            await client.get("https://other.test/api/v1/servers")

    async def test_does_not_follow_redirects_with_bearer_credential(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                307, headers={"Location": "https://other.test/api/v1/servers"}
            )

        client = AgentsWatchAPIClient(
            "http://api.test",
            "Bearer aw_test",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(AgentsWatchAPIError) as raised:
            await client.get("/api/v1/servers")

        self.assertEqual(raised.exception.code, "UPSTREAM_REDIRECT_REJECTED")
        self.assertEqual(len(requests), 1)

    @unittest.skipUnless(
        hasattr(asyncio, "start_unix_server"), "Unix sockets are unavailable"
    )
    async def test_uses_unix_socket_for_normal_http_rest_request(self):
        received = []

        async def handle(reader, writer):
            received.append(await reader.readuntil(b"\r\n\r\n"))
            body = b'{"data":{"server_id":"server-1"}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        with tempfile.TemporaryDirectory() as directory:
            socket_path = os.path.join(directory, "api.sock")
            server = await asyncio.start_unix_server(handle, path=socket_path)
            try:
                client = AgentsWatchAPIClient(
                    "http://agentswatch/management",
                    "Bearer aw_uds_secret",
                    uds=socket_path,
                )
                result = await client.get("/api/v1/servers/server-1")
            finally:
                server.close()
                await server.wait_closed()

        self.assertEqual(result["data"]["server_id"], "server-1")
        request = received[0].decode("latin-1")
        self.assertIn("GET /management/api/v1/servers/server-1 HTTP/1.1", request)
        self.assertIn("Host: agentswatch", request)
        self.assertIn("Authorization: Bearer aw_uds_secret", request)

    async def test_uds_requires_an_absolute_path_and_excludes_custom_transport(self):
        with self.assertRaisesRegex(ValueError, "absolute Unix socket"):
            AgentsWatchAPIClient("http://api.test", "Bearer aw_test", uds="api.sock")
        with self.assertRaisesRegex(ValueError, "cannot be configured together"):
            AgentsWatchAPIClient(
                "http://api.test",
                "Bearer aw_test",
                uds="/run/agentswatch/api.sock",
                transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            )

    async def test_settings_read_optional_uds_without_changing_tcp_default(self):
        with patch.dict(os.environ, {}, clear=True):
            defaults = MCPSettings.from_environment()
        self.assertIsNone(defaults.api_uds)
        self.assertEqual(defaults.api_base_url, "http://127.0.0.1:8000")

        with patch.dict(
            os.environ,
            {
                "AGENTSWATCH_API_BASE_URL": "http://agentswatch",
                "AGENTSWATCH_API_UDS": "/run/agentswatch/api.sock",
            },
            clear=True,
        ):
            configured = MCPSettings.from_environment()
        self.assertEqual(configured.api_base_url, "http://agentswatch")
        self.assertEqual(configured.api_uds, "/run/agentswatch/api.sock")

        with patch.dict(
            os.environ, {"AGENTSWATCH_API_UDS": "run/api.sock"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "absolute Unix socket"):
                MCPSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
