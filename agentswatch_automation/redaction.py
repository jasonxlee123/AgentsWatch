import os
import re
from typing import Any


URI_CREDENTIAL = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)")
BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{8,})")
NAMED_SECRET = re.compile(
    r'''(?ix)
    ((?:\\?["'])?\b(?:password|passwd|pwd|token|secret|app[_-]?secret|api[_-]?key|access[_-]?key|authorization)\b
    (?:\\?["'])?\s*[=:]\s*)
    ("[^"]*"|'[^']*'|[^\s,;\]}]+)
    ''',
)
SECRET_FIELD = re.compile(
    r"(?i)^(?:password|passwd|pwd|token|secret|app[_-]?secret|api[_-]?key|access[_-]?key|authorization)$",
)


def _known_secret_values():
    values = set()
    for name, value in os.environ.items():
        upper = name.upper()
        if not value or len(value) < 4:
            continue
        if any(marker in upper for marker in ("PASSWORD", "PASSWD", "TOKEN", "SECRET", "CREDENTIAL_KEY", "DATABASE_URL")):
            values.add(value)
    return sorted(values, key=len, reverse=True)


def redact_sensitive_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = URI_CREDENTIAL.sub(r"\1[REDACTED]\3", text)
    text = BEARER_TOKEN.sub(r"\1[REDACTED]", text)
    text = NAMED_SECRET.sub(r"\1[REDACTED]", text)
    for secret in _known_secret_values():
        text = text.replace(secret, "[REDACTED]")
    return text


def redact_sensitive_data(value: Any):
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]" if SECRET_FIELD.fullmatch(str(key))
                else redact_sensitive_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value
