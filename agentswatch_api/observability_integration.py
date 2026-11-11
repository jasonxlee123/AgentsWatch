from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx


class ObservabilityIntegrationError(RuntimeError):
    """A safe, user-facing failure while calling the operations adapter."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ObservabilityIntegrationClient:
    """Small, deliberately narrow client for the independently deployed adapter."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or os.environ.get(
            "AGENTSWATCH_OBSERVABILITY_CONTROL_URL", ""
        )).strip().rstrip("/")
        self.token = token if token is not None else os.environ.get(
            "AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN", ""
        )
        self.transport = transport
        if self.base_url:
            self._validate_base_url(self.base_url)

    @staticmethod
    def _validate_base_url(value: str) -> None:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ObservabilityIntegrationError(
                503, "运维适配器地址配置无效，请检查服务端配置。"
            )

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ObservabilityIntegrationError(
                503, "尚未配置独立运维适配器，脚本模式仍可继续使用。"
            )
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        timeout = httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=2.0)
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = client.request(method, path, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ObservabilityIntegrationError(
                503, "运维适配器当前不可用，管理数据和已激活的运维配置均未改变。"
            ) from exc

        if response.is_redirect:
            raise ObservabilityIntegrationError(
                502, "运维适配器返回了不受支持的重定向。"
            )
        if response.status_code >= 500:
            raise ObservabilityIntegrationError(
                502, "运维适配器执行失败，已激活版本保持不变。"
            )
        if response.status_code >= 400:
            detail = "运维适配器拒绝了本次请求。"
            try:
                upstream = response.json()
                if isinstance(upstream, dict) and isinstance(upstream.get("detail"), str):
                    detail = upstream["detail"][:500]
            except ValueError:
                pass
            raise ObservabilityIntegrationError(response.status_code, detail)
        try:
            result = response.json()
        except ValueError as exc:
            raise ObservabilityIntegrationError(
                502, "运维适配器返回了无效响应。"
            ) from exc
        if not isinstance(result, dict):
            raise ObservabilityIntegrationError(
                502, "运维适配器返回了无效响应。"
            )
        return result

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "adapter_status": "not_configured",
                "source_status": "unknown",
                "active_revision": None,
                "last_success_at": None,
                "gateway_count": 0,
                "blocked_count": 0,
                "jobs": [],
                "message": "管理模块尚未配置独立运维适配器；运维脚本模式不受影响。",
            }
        result = self._request("GET", "/api/v1/inventory/status")
        result.setdefault("adapter_status", "connected")
        return result

    def preview(self, source: str) -> dict[str, Any]:
        return self._request(
            "POST", "/api/v1/inventory/sync:preview", {"source": source}
        )

    def apply(self, source: str, expected_snapshot_hash: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/inventory/sync",
            {"source": source, "expected_snapshot_hash": expected_snapshot_hash},
        )

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/jobs/{job_id}")

    def rollback(self, revision_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/v1/inventory/rollback/{revision_id}", {}
        )
