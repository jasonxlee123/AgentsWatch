"""Environment configuration for the standalone MCP process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _boolean(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _port(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


@dataclass(frozen=True, slots=True)
class MCPSettings:
    api_base_url: str = "http://127.0.0.1:8000"
    api_uds: str | None = None
    request_timeout: float = 30.0
    tls_verify: bool = True
    host: str = "127.0.0.1"
    port: int = 8010
    log_level: str = "info"

    def __post_init__(self) -> None:
        if self.api_uds is not None and (
            not self.api_uds
            or "\x00" in self.api_uds
            or not Path(self.api_uds).is_absolute()
        ):
            raise ValueError("AGENTSWATCH_API_UDS must be an absolute Unix socket path")

    @classmethod
    def from_environment(cls) -> "MCPSettings":
        log_level = os.environ.get("AGENTSWATCH_MCP_LOG_LEVEL", "info").strip().lower()
        if log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
            raise ValueError("AGENTSWATCH_MCP_LOG_LEVEL is invalid")
        api_uds = os.environ.get("AGENTSWATCH_API_UDS", "").strip() or None
        return cls(
            api_base_url=os.environ.get(
                "AGENTSWATCH_API_BASE_URL", "http://127.0.0.1:8000"
            ).strip(),
            api_uds=api_uds,
            request_timeout=_positive_float(
                "AGENTSWATCH_MCP_REQUEST_TIMEOUT", 30.0
            ),
            tls_verify=_boolean("AGENTSWATCH_MCP_TLS_VERIFY", True),
            host=os.environ.get("AGENTSWATCH_MCP_HOST", "127.0.0.1").strip(),
            port=_port("AGENTSWATCH_MCP_PORT", 8010),
            log_level=log_level,
        )
