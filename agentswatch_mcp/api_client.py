"""Minimal asynchronous client for the AgentsWatch REST API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from .errors import AgentsWatchAPIError


class AgentsWatchAPIClient:
    """Call AgentsWatch exclusively through its versioned REST API."""

    ALLOWED_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        timeout: float = 30.0,
        verify: bool = True,
        uds: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AGENTSWATCH_API_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment")
        if not bearer_token.lower().startswith("bearer ") or not bearer_token[7:].strip():
            raise ValueError("bearer_token must contain a complete Bearer Authorization header")
        if uds is not None:
            if not uds or "\x00" in uds or not Path(uds).is_absolute():
                raise ValueError("uds must be an absolute Unix socket path")
            if transport is not None:
                raise ValueError("uds and transport cannot be configured together")

        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout = timeout
        self.verify = verify
        self.uds = uds
        self.transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        confirmation_id: str | None = None,
    ) -> Any:
        method = method.upper()
        if method not in self.ALLOWED_METHODS:
            raise ValueError(f"unsupported AgentsWatch API method: {method}")
        if not path.startswith("/api/v1/") or "://" in path:
            raise ValueError("AgentsWatch API paths must start with /api/v1/")

        headers = {
            "Authorization": self.bearer_token,
            "Accept": "application/json",
        }
        if confirmation_id:
            headers["X-Confirmation-ID"] = confirmation_id

        try:
            transport = self.transport
            if self.uds is not None:
                transport = httpx.AsyncHTTPTransport(
                    uds=self.uds, verify=self.verify
                )
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=self.verify,
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params={key: value for key, value in (params or {}).items() if value is not None},
                    json=json,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise AgentsWatchAPIError(
                504, "UPSTREAM_TIMEOUT", "AgentsWatch API request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentsWatchAPIError(
                502, "UPSTREAM_UNAVAILABLE", "AgentsWatch API is unavailable"
            ) from exc

        if 300 <= response.status_code < 400:
            raise AgentsWatchAPIError(
                502,
                "UPSTREAM_REDIRECT_REJECTED",
                "AgentsWatch API returned an unexpected redirect",
            )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {"detail": response.text[:1000]}
            raise AgentsWatchAPIError.from_response(response.status_code, payload)

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AgentsWatchAPIError(
                502,
                "INVALID_UPSTREAM_RESPONSE",
                "AgentsWatch API returned a non-JSON response",
            ) from exc

    async def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        json: Any,
        confirmation_id: str | None = None,
    ) -> Any:
        return await self.request(
            "POST", path, json=json, confirmation_id=confirmation_id
        )

    async def patch(
        self,
        path: str,
        *,
        json: Any,
        confirmation_id: str | None = None,
    ) -> Any:
        return await self.request(
            "PATCH", path, json=json, confirmation_id=confirmation_id
        )

    async def put(
        self,
        path: str,
        *,
        json: Any,
        confirmation_id: str | None = None,
    ) -> Any:
        return await self.request(
            "PUT", path, json=json, confirmation_id=confirmation_id
        )

    async def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        confirmation_id: str | None = None,
    ) -> Any:
        return await self.request(
            "DELETE", path, params=params, confirmation_id=confirmation_id
        )
