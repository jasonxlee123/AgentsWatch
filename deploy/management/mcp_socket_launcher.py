#!/usr/bin/env python3
"""Bind the MCP UDS with strict permissions, then exec one Uvicorn worker."""

import os
import socket
import stat
import sys
from pathlib import Path


SOCKET_PATH = Path("/data/agentswatch/management/run/mcp.sock")
SOCKET_MODE = 0o660


def remove_stale_socket(path: Path) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(current.st_mode):
        raise RuntimeError(f"refusing to replace non-socket path: {path}")
    path.unlink()


def main() -> None:
    parent = SOCKET_PATH.parent.resolve(strict=True)
    expected_parent = Path("/data/agentswatch/management/run").resolve(strict=True)
    if parent != expected_parent:
        raise RuntimeError(f"management socket directory resolves unexpectedly: {parent}")

    remove_stale_socket(SOCKET_PATH)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, SOCKET_MODE)
    listener.listen(2048)
    os.set_inheritable(listener.fileno(), True)

    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        "agentswatch_mcp.server:app",
        "--fd",
        str(listener.fileno()),
        "--workers",
        "1",
        "--proxy-headers",
        "--forwarded-allow-ips=*",
    ]
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
