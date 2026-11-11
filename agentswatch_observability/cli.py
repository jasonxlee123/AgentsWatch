from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import uvicorn

from .api import create_app
from .config import ControlConfig
from .control import InventoryController


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _config(args: argparse.Namespace) -> ControlConfig:
    config = ControlConfig.from_env()
    updates = {}
    for argument, field_name, converter in (
        ("state_root", "state_root", Path),
        ("inventory", "local_inventory_path", Path),
        ("overlay", "overlay_path", Path),
        ("database_url", "database_url", str),
        ("otel_endpoint", "otel_endpoint", str),
        ("runtime_family", "runtime_family", str),
        ("prometheus_reload_url", "prometheus_reload_url", str),
    ):
        value = getattr(args, argument, None)
        if value is not None:
            updates[field_name] = converter(value)
    return replace(config, **updates)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-root")
    parser.add_argument("--inventory")
    parser.add_argument("--overlay")
    parser.add_argument("--database-url")
    parser.add_argument("--otel-endpoint")
    parser.add_argument("--runtime-family")
    parser.add_argument("--prometheus-reload-url")


def _add_sync_request(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source", choices=("management-db", "local-json"), default="management-db"
    )
    parser.add_argument("--expected-snapshot-hash")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operate the independent AgentsWatch observability inventory adapter."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    _add_common(status_parser)

    preview_parser = subparsers.add_parser("preview")
    _add_common(preview_parser)
    _add_sync_request(preview_parser)

    sync_parser = subparsers.add_parser("sync")
    _add_common(sync_parser)
    _add_sync_request(sync_parser)

    rollback_parser = subparsers.add_parser("rollback")
    _add_common(rollback_parser)
    rollback_parser.add_argument("revision_id")

    serve_parser = subparsers.add_parser("serve")
    _add_common(serve_parser)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8011)

    args = parser.parse_args(argv)
    controller = InventoryController(_config(args))
    if args.command == "status":
        _print(controller.status())
    elif args.command == "preview":
        _print(controller.preview(args.source, args.expected_snapshot_hash))
    elif args.command == "sync":
        _print(controller.sync(args.source, args.expected_snapshot_hash))
    elif args.command == "rollback":
        _print(controller.rollback(args.revision_id))
    elif args.command == "serve":
        uvicorn.run(create_app(controller), host=args.host, port=args.port, workers=1)
    return 0
