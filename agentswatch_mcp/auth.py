"""Request-scoped bearer-token forwarding for Streamable HTTP MCP calls."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from starlette.types import ASGIApp, Receive, Scope, Send


_bearer_token: ContextVar[str | None] = ContextVar(
    "agentswatch_mcp_bearer_token", default=None
)


class MissingBearerToken(RuntimeError):
    """Raised when a REST-backed tool is called without a bearer credential."""


def current_bearer_token() -> str:
    """Return the complete Authorization header for the current MCP request."""

    token = _bearer_token.get()
    if token is None:
        raise MissingBearerToken("a Bearer API key is required")
    return token


@contextmanager
def bearer_token_context(value: str) -> Iterator[None]:
    """Set a bearer token for an isolated execution context.

    This is primarily useful for non-HTTP protocol tests. Production requests are
    populated by :class:`BearerForwardingMiddleware`.
    """

    token = _bearer_token.set(value)
    try:
        yield
    finally:
        _bearer_token.reset(token)


def _authorization_header(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            try:
                return value.decode("latin-1")
            except UnicodeDecodeError:
                return None
    return None


def _is_bearer(value: str | None) -> bool:
    if not value:
        return False
    scheme, separator, credential = value.partition(" ")
    return bool(separator and scheme.lower() == "bearer" and credential.strip())


class BearerForwardingMiddleware:
    """Require bearer auth on MCP transport requests and expose it to tools.

    The credential is deliberately not parsed, validated, persisted, or logged by
    this service. AgentsWatch remains the only authentication authority.
    """

    def __init__(self, app: ASGIApp, protected_path: str = "/mcp") -> None:
        self.app = app
        self.protected_path = protected_path.rstrip("/") or "/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", "")).rstrip("/") or "/"
        if path != self.protected_path:
            await self.app(scope, receive, send)
            return

        authorization = _authorization_header(scope)
        if not _is_bearer(authorization):
            body = b'{"error":{"code":"AUTHENTICATION_REQUIRED","message":"a Bearer API key is required"}}'
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"www-authenticate", b"Bearer"),
            ]
            await send({"type": "http.response.start", "status": 401, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        context_token = _bearer_token.set(authorization)
        try:
            await self.app(scope, receive, send)
        finally:
            _bearer_token.reset(context_token)
