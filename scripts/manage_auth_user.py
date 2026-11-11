#!/usr/bin/env python3
"""Create the first AgentsWatch application user without exposing a password in argv."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import psycopg


for candidate in (Path(__file__).resolve().parents[1], Path("/opt/agentswatch")):
    if (candidate / "agentswatch_api").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from agentswatch_api.auth import AuthService  # noqa: E402


def read_password(use_stdin: bool) -> str:
    if use_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    else:
        value = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if value != confirmation:
            raise ValueError("password confirmation does not match")
    if not value:
        raise ValueError("password is required")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", choices=("admin", "operator", "viewer"), default="admin")
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    try:
        created = AuthService().create_user(
            args.username,
            args.display_name,
            read_password(args.password_stdin),
            args.role,
            "bootstrap-cli",
        )
        print(json.dumps(created, ensure_ascii=False, default=str))
        return 0
    except psycopg.errors.UniqueViolation:
        print("user already exists", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError, psycopg.Error) as exc:
        print(f"failed to create user: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
