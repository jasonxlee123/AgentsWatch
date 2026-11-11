"""Shared tool execution helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from ..api_client import AgentsWatchAPIClient
from ..auth import MissingBearerToken
from ..errors import AgentsWatchAPIError


ClientFactory: TypeAlias = Callable[[], AgentsWatchAPIClient]
APICall: TypeAlias = Callable[[], Awaitable[Any]]


async def tool_result(call: APICall) -> dict[str, Any]:
    """Execute a REST call and preserve expected failures as structured data."""

    try:
        return {"ok": True, "result": await call()}
    except AgentsWatchAPIError as exc:
        return exc.as_tool_result()
    except MissingBearerToken as exc:
        return {
            "ok": False,
            "error": {
                "status_code": 401,
                "code": "AUTHENTICATION_REQUIRED",
                "message": str(exc),
            },
        }


def mutation_payload(changes: dict[str, Any], **required: Any) -> dict[str, Any]:
    """Merge caller-controlled fields without allowing required values to be replaced."""

    payload = dict(changes)
    payload.update(required)
    return payload
