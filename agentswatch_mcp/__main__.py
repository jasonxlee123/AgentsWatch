"""Run the AgentsWatch MCP service with Uvicorn."""

import uvicorn

from .server import app, settings


def main() -> None:
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
