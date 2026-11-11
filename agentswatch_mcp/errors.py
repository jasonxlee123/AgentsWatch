"""Structured REST-to-MCP error translation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


_DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    502: "UPSTREAM_UNAVAILABLE",
    503: "UPSTREAM_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass(slots=True)
class AgentsWatchAPIError(RuntimeError):
    """An HTTP or transport failure returned by AgentsWatch."""

    status_code: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    confirmation_id: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def as_tool_result(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "status_code": self.status_code,
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            error["details"] = self.details
        if self.confirmation_id:
            error["confirmation"] = {
                "confirmation_id": self.confirmation_id,
                **({"expires_at": self.expires_at} if self.expires_at else {}),
            }
        return {"ok": False, "error": error}

    @classmethod
    def from_response(cls, status_code: int, payload: Any) -> "AgentsWatchAPIError":
        root = _mapping(payload)
        detail = root.get("detail")
        error = _first_mapping(root.get("error"), detail, root)
        confirmation = _first_mapping(
            error.get("confirmation"),
            root.get("confirmation"),
            detail if isinstance(detail, Mapping) else None,
        )

        code = _first_text(
            error.get("code"),
            root.get("code"),
            detail.get("code") if isinstance(detail, Mapping) else None,
        ) or _DEFAULT_CODES.get(status_code, "AGENTSWATCH_API_ERROR")
        code = code.upper()
        message = _first_text(
            error.get("message"),
            root.get("message"),
            detail.get("message") if isinstance(detail, Mapping) else None,
            detail,
        ) or f"AgentsWatch API returned HTTP {status_code}"
        confirmation_id = _first_text(
            confirmation.get("confirmation_id"),
            confirmation.get("id"),
            error.get("confirmation_id"),
            root.get("confirmation_id"),
        )
        expires_at = _first_text(
            confirmation.get("expires_at"),
            error.get("expires_at"),
            root.get("expires_at"),
        )

        details = dict(error)
        for key in ("code", "message", "confirmation", "confirmation_id", "expires_at"):
            details.pop(key, None)
        if not details and isinstance(detail, list):
            details = {"validation_errors": detail}

        error_type: type[AgentsWatchAPIError] = cls
        if code == "CONFIRMATION_REQUIRED" or (
            status_code == 409 and confirmation_id is not None
        ):
            error_type = ConfirmationRequiredError
        return error_type(
            status_code=status_code,
            code=code,
            message=message,
            details=details,
            confirmation_id=confirmation_id,
            expires_at=expires_at,
        )


class ConfirmationRequiredError(AgentsWatchAPIError):
    """A single-use confirmation must be supplied to retry the operation."""
