import json
import os
import unittest
from unittest.mock import patch

import httpx

from agentswatch_api.observability_integration import (
    ObservabilityIntegrationClient,
    ObservabilityIntegrationError,
)


class ObservabilityIntegrationClientTests(unittest.TestCase):
    def test_unconfigured_status_keeps_standalone_mode_available(self):
        with patch.dict(os.environ, {}, clear=True):
            status = ObservabilityIntegrationClient().status()

        self.assertEqual(status["adapter_status"], "not_configured")
        self.assertIsNone(status["active_revision"])
        self.assertIn("脚本模式", status["message"])

    def test_preview_uses_fixed_path_and_adapter_bearer_token(self):
        captured = []

        def handler(request):
            captured.append(request)
            return httpx.Response(
                200,
                json={"snapshot_hash": "a" * 64, "summary": {"add": 1}},
            )

        client = ObservabilityIntegrationClient(
            "http://ops-adapter.internal:9088",
            "adapter-secret",
            transport=httpx.MockTransport(handler),
        )
        result = client.preview("management-db")

        self.assertEqual(result["snapshot_hash"], "a" * 64)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].url.path, "/api/v1/inventory/sync:preview")
        self.assertEqual(captured[0].headers["authorization"], "Bearer adapter-secret")
        self.assertEqual(json.loads(captured[0].content), {"source": "management-db"})

    def test_network_failure_is_safe_service_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        client = ObservabilityIntegrationClient(
            "http://127.0.0.1:9", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(ObservabilityIntegrationError) as raised:
            client.status()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("已激活", raised.exception.detail)

    def test_upstream_error_only_exposes_bounded_detail(self):
        client = ObservabilityIntegrationClient(
            "http://ops-adapter.internal",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(409, json={"detail": "预览已过期"})
            ),
        )
        with self.assertRaises(ObservabilityIntegrationError) as raised:
            client.apply("management-db", "f" * 64)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "预览已过期")

    def test_credentials_are_rejected_in_adapter_url(self):
        with self.assertRaises(ObservabilityIntegrationError) as raised:
            ObservabilityIntegrationClient("http://user:password@ops.internal")

        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
