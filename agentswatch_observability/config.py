from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ControlConfig:
    state_root: Path
    local_inventory_path: Path
    overlay_path: Path
    database_url: str
    otel_endpoint: str
    runtime_family: str = "openclaw"
    prometheus_reload_url: str = ""
    allow_empty_inventory: bool = False
    allow_destructive_inventory: bool = False

    @classmethod
    def from_env(cls) -> "ControlConfig":
        state_root = Path(
            os.environ.get(
                "AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT",
                "/data/agentswatch/observability-control",
            )
        )
        return cls(
            state_root=state_root,
            local_inventory_path=Path(
                os.environ.get(
                    "AGENTSWATCH_OBSERVABILITY_LOCAL_INVENTORY",
                    "cluster/lobsters.json",
                )
            ),
            overlay_path=Path(
                os.environ.get(
                    "AGENTSWATCH_OBSERVABILITY_OVERLAY",
                    str(state_root / "overlay.json"),
                )
            ),
            database_url=os.environ.get(
                "AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL", ""
            ).strip(),
            otel_endpoint=os.environ.get("AGENTSWATCH_OTEL_ENDPOINT", "").strip(),
            runtime_family=os.environ.get(
                "AGENTSWATCH_OBSERVABILITY_RUNTIME_FAMILY", "openclaw"
            ).strip()
            or "openclaw",
            prometheus_reload_url=os.environ.get(
                "AGENTSWATCH_PROMETHEUS_RELOAD_URL", ""
            ).strip(),
            allow_empty_inventory=os.environ.get(
                "AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            allow_destructive_inventory=os.environ.get(
                "AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
        )
