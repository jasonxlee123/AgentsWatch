#!/usr/bin/env python3
"""Relay one Unix socket to the host's loopback-only OpenClaw endpoint."""

from __future__ import annotations

import asyncio
import os
import signal
import stat
from contextlib import suppress
from pathlib import Path


SOCKET_PATH = Path(
    os.environ.get(
        "AGENTSWATCH_OPENCLAW_RELAY_SOCKET",
        "/data/agentswatch/management/run/openclaw.sock",
    )
)
UPSTREAM_HOST = os.environ.get("AGENTSWATCH_OPENCLAW_RELAY_HOST", "127.0.0.1").strip()
UPSTREAM_PORT = int(os.environ.get("AGENTSWATCH_OPENCLAW_RELAY_PORT", "16402"))


async def copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    finally:
        with suppress(BrokenPipeError, ConnectionError, RuntimeError):
            writer.write_eof()


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    upstream_writer = None
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            UPSTREAM_HOST, UPSTREAM_PORT
        )
        await asyncio.gather(
            copy_stream(reader, upstream_writer),
            copy_stream(upstream_reader, writer),
        )
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            with suppress(ConnectionError):
                await upstream_writer.wait_closed()
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()


def prepare_socket_path() -> None:
    if UPSTREAM_HOST != "127.0.0.1":
        raise ValueError("OpenClaw compatibility relay upstream must remain 127.0.0.1")
    if not 1 <= UPSTREAM_PORT <= 65535:
        raise ValueError("OpenClaw compatibility relay port is outside 1..65535")
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata = SOCKET_PATH.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("refusing to replace a symlinked OpenClaw relay socket")
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError("refusing to replace a non-socket OpenClaw relay path")
    SOCKET_PATH.unlink()


async def serve() -> None:
    prepare_socket_path()
    server = await asyncio.start_unix_server(handle_client, path=SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o660)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stopped.set)
    try:
        await stopped.wait()
    finally:
        server.close()
        await server.wait_closed()
        SOCKET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(serve())
