"""Server-side, one-time confirmation challenges for high-risk HTTP operations.

The service is deliberately HTTP-framework agnostic. Route handlers provide the
authenticated user and request components, then translate ``ConfirmationError``
instances into their API response format.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from .database import WriteDatabase


CONFIRMATION_HEADER = "X-Confirmation-ID"
CONFIRMATION_ID_PREFIX = "cfm_"
DEFAULT_CONFIRMATION_TTL_SECONDS = 300
MAX_CONFIRMATION_TTL_SECONDS = 900


class ConfirmationError(RuntimeError):
    """Base error that route handlers can translate without parsing messages."""

    code = "CONFIRMATION_INVALID"

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


class ConfirmationRequired(ConfirmationError):
    """Raised after preparing the challenge for an unconfirmed request."""

    code = "CONFIRMATION_REQUIRED"

    def __init__(self, confirmation: "OperationConfirmation"):
        super().__init__("此操作需要二次确认")
        self.confirmation = confirmation

    def as_detail(self) -> dict[str, Any]:
        return {
            **super().as_detail(),
            "confirmation_id": self.confirmation.public_id,
            "confirmation_header": CONFIRMATION_HEADER,
            "expires_at": self.confirmation.expires_at.isoformat(),
        }


class ConfirmationRejected(ConfirmationError):
    """Raised for malformed, expired, used, or request-mismatched challenges."""

    code = "CONFIRMATION_INVALID"

    def __init__(self):
        # Do not reveal whether an ID belongs to another user or request.
        super().__init__("确认已失效或与当前操作不匹配，请重新确认")


@dataclass(frozen=True)
class RequestFingerprint:
    method: str
    path: str
    query_hash: str
    body_hash: str


@dataclass(frozen=True)
class OperationConfirmation:
    confirmation_id: UUID
    user_id: UUID
    fingerprint: RequestFingerprint
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime

    @property
    def public_id(self) -> str:
        return format_confirmation_id(self.confirmation_id)


def format_confirmation_id(confirmation_id: UUID | str) -> str:
    """Return the opaque, namespaced ID exposed through the HTTP API."""

    return CONFIRMATION_ID_PREFIX + UUID(str(confirmation_id)).hex


def parse_confirmation_id(value: str) -> UUID:
    """Parse a public ID and reject unnamespaced UUIDs fail-closed."""

    if not isinstance(value, str) or not value.startswith(CONFIRMATION_ID_PREFIX):
        raise ConfirmationRejected()
    encoded = value[len(CONFIRMATION_ID_PREFIX):]
    if len(encoded) != 32:
        raise ConfirmationRejected()
    try:
        return UUID(hex=encoded)
    except ValueError as exc:
        raise ConfirmationRejected() from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"request component contains unsupported value: {type(value).__name__}")


def _component_bytes(value: Any) -> bytes:
    """Encode a request component deterministically without storing its contents."""

    if isinstance(value, bytes):
        return b"bytes\x00" + value
    if isinstance(value, bytearray):
        return b"bytes\x00" + bytes(value)
    if isinstance(value, str):
        return b"text\x00" + value.encode("utf-8")
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "multi_items"):
        value = list(value.multi_items())
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return b"json\x00" + encoded


def request_component_hash(value: Any) -> str:
    return hashlib.sha256(_component_bytes(value)).hexdigest()


def fingerprint_request(
    method: str,
    path: str,
    query: Any = "",
    body: Any = b"",
) -> RequestFingerprint:
    """Build the exact binding persisted for a confirmation challenge.

    HTTP integrations should pass ``request.url.query`` for the query component
    and a Pydantic ``model_dump(mode="json")`` (or the raw bytes) for the body.
    """

    normalized_method = str(method).strip().upper()
    if not normalized_method.isalpha() or len(normalized_method) > 16:
        raise ValueError("request method must contain 1-16 ASCII letters")
    if not normalized_method.isascii():
        raise ValueError("request method must contain 1-16 ASCII letters")
    normalized_path = str(path)
    if (
        not normalized_path.startswith("/")
        or "?" in normalized_path
        or "#" in normalized_path
        or len(normalized_path) > 2048
    ):
        raise ValueError("request path must be an absolute URL path without query or fragment")
    return RequestFingerprint(
        method=normalized_method,
        path=normalized_path,
        query_hash=request_component_hash(query),
        body_hash=request_component_hash(body),
    )


class ConfirmationService:
    """Prepare, verify, and atomically consume operation confirmations."""

    def __init__(self, database=None, ttl_seconds: int = DEFAULT_CONFIRMATION_TTL_SECONDS):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValueError("confirmation TTL must be a whole number of seconds")
        if not 1 <= ttl_seconds <= MAX_CONFIRMATION_TTL_SECONDS:
            raise ValueError(
                f"confirmation TTL must be between 1 and {MAX_CONFIRMATION_TTL_SECONDS} seconds"
            )
        self.database = database or WriteDatabase()
        self.ttl = timedelta(seconds=ttl_seconds)

    @staticmethod
    def _request(user_id: UUID | str, method: str, path: str, query: Any, body: Any):
        try:
            normalized_user_id = UUID(str(user_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("user_id must be a UUID") from exc
        return normalized_user_id, fingerprint_request(method, path, query, body)

    @staticmethod
    def _parameters(
        confirmation_id: UUID | None,
        user_id: UUID,
        fingerprint: RequestFingerprint,
    ) -> dict[str, Any]:
        return {
            "confirmation_id": confirmation_id,
            "user_id": user_id,
            "request_method": fingerprint.method,
            "request_path": fingerprint.path,
            "query_hash": fingerprint.query_hash,
            "body_hash": fingerprint.body_hash,
        }

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> OperationConfirmation:
        return OperationConfirmation(
            confirmation_id=UUID(str(row["confirmation_id"])),
            user_id=UUID(str(row["user_id"])),
            fingerprint=RequestFingerprint(
                method=row["request_method"],
                path=row["request_path"],
                query_hash=str(row["query_hash"]).strip(),
                body_hash=str(row["body_hash"]).strip(),
            ),
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            created_at=row["created_at"],
        )

    def prepare(
        self,
        user_id: UUID | str,
        method: str,
        path: str,
        query: Any = "",
        body: Any = b"",
    ) -> OperationConfirmation:
        """Create a short-lived challenge bound to one authenticated request."""

        normalized_user_id, fingerprint = self._request(user_id, method, path, query, body)
        params = self._parameters(None, normalized_user_id, fingerprint)
        params["ttl"] = self.ttl
        with self.database.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO security.operation_confirmation (
                    user_id, request_method, request_path, query_hash, body_hash, expires_at
                ) VALUES (
                    %(user_id)s, %(request_method)s, %(request_path)s,
                    %(query_hash)s, %(body_hash)s, now() + %(ttl)s
                )
                RETURNING confirmation_id, user_id, request_method, request_path,
                          query_hash, body_hash, expires_at, used_at, created_at
                """,
                params,
            ).fetchone()
        return self._from_row(row)

    def verify(
        self,
        confirmation_id: str,
        user_id: UUID | str,
        method: str,
        path: str,
        query: Any = "",
        body: Any = b"",
    ) -> OperationConfirmation:
        """Check an active challenge without consuming it.

        Authorization gates should not rely on this non-consuming method. Use
        ``consume`` or ``require`` immediately before the protected mutation.
        """

        internal_id = parse_confirmation_id(confirmation_id)
        normalized_user_id, fingerprint = self._request(user_id, method, path, query, body)
        params = self._parameters(internal_id, normalized_user_id, fingerprint)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT confirmation_id, user_id, request_method, request_path,
                       query_hash, body_hash, expires_at, used_at, created_at
                FROM security.operation_confirmation
                WHERE confirmation_id = %(confirmation_id)s
                  AND user_id = %(user_id)s
                  AND request_method = %(request_method)s
                  AND request_path = %(request_path)s
                  AND query_hash = %(query_hash)s
                  AND body_hash = %(body_hash)s
                  AND used_at IS NULL
                  AND expires_at > now()
                """,
                params,
            ).fetchone()
        if row is None:
            raise ConfirmationRejected()
        return self._from_row(row)

    def consume(
        self,
        confirmation_id: str,
        user_id: UUID | str,
        method: str,
        path: str,
        query: Any = "",
        body: Any = b"",
    ) -> OperationConfirmation:
        """Atomically validate and consume a challenge exactly once."""

        internal_id = parse_confirmation_id(confirmation_id)
        normalized_user_id, fingerprint = self._request(user_id, method, path, query, body)
        params = self._parameters(internal_id, normalized_user_id, fingerprint)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                UPDATE security.operation_confirmation
                SET used_at = now()
                WHERE confirmation_id = %(confirmation_id)s
                  AND user_id = %(user_id)s
                  AND request_method = %(request_method)s
                  AND request_path = %(request_path)s
                  AND query_hash = %(query_hash)s
                  AND body_hash = %(body_hash)s
                  AND used_at IS NULL
                  AND expires_at > now()
                RETURNING confirmation_id, user_id, request_method, request_path,
                          query_hash, body_hash, expires_at, used_at, created_at
                """,
                params,
            ).fetchone()
        if row is None:
            raise ConfirmationRejected()
        return self._from_row(row)

    def require(
        self,
        confirmation_id: str | None,
        user_id: UUID | str,
        method: str,
        path: str,
        query: Any = "",
        body: Any = b"",
    ) -> OperationConfirmation:
        """Prepare-and-signal or validate-and-consume in one route-friendly call."""

        if confirmation_id is None:
            raise ConfirmationRequired(self.prepare(user_id, method, path, query, body))
        return self.consume(confirmation_id, user_id, method, path, query, body)
