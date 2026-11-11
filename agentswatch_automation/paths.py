import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_AUTOMATION_ROOT = ROOT / "automation"
AUTOMATION_DATA_ROOT_ENV = "AGENTSWATCH_AUTOMATION_DATA_ROOT"


def automation_data_root() -> Path:
    """Return the mutable automation-data root.

    The code checkout remains the default for local development. Production can
    move mutable data to persistent storage without changing the logical paths
    stored in the database.
    """
    configured = os.environ.get(AUTOMATION_DATA_ROOT_ENV, "").strip()
    if not configured:
        return CODE_AUTOMATION_ROOT
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{AUTOMATION_DATA_ROOT_ENV} must be an absolute path")
    return path.resolve()


def managed_root() -> Path:
    return automation_data_root() / "managed"


def artifact_root() -> Path:
    return automation_data_root() / "artifacts"


def runner_root() -> Path:
    return automation_data_root() / ".runner"


def resolve_managed_playbook(logical_path: str | Path) -> Path:
    """Resolve a database-managed logical path below the configured root."""
    relative = Path(logical_path)
    if relative.is_absolute() or len(relative.parts) < 2 or relative.parts[0] != "managed":
        raise ValueError(f"invalid managed playbook path: {logical_path}")
    root = managed_root().resolve()
    target = (root / Path(*relative.parts[1:])).resolve()
    if root not in target.parents:
        raise ValueError(f"managed playbook is outside configured root: {logical_path}")
    return target
