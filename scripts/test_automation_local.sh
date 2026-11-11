#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv-automation"

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import ansible_runner, psycopg' >/dev/null 2>&1; then
  "$VENV/bin/pip" install -r "$ROOT/requirements-automation.txt"
fi

cd "$ROOT"
"$VENV/bin/python" -m unittest \
  tests.test_automation_catalog \
  tests.test_automation_runner_local \
  -v
