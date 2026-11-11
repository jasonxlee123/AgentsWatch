import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles


STATIC_DIR_ENV = "AGENTSWATCH_STATIC_DIR"


def configured_static_directory() -> Path | None:
    """Resolve an optional, pre-built frontend directory for deployments."""
    configured = os.environ.get(STATIC_DIR_ENV, "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{STATIC_DIR_ENV} must be an absolute path")
    path = path.resolve()
    if not path.is_dir() or not (path / "index.html").is_file():
        raise RuntimeError(f"{STATIC_DIR_ENV} must contain a built index.html")
    return path


def mount_configured_frontend(app) -> Path | None:
    directory = configured_static_directory()
    if directory is not None:
        app.mount("/", StaticFiles(directory=directory, html=True), name="management-frontend")
    return directory
