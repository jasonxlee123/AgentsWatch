#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentswatch_automation import AutomationRunner  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Run an approved AgentsWatch automation operation.")
    parser.add_argument("operation_code")
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    args = parser.parse_args()

    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    parameters = {}
    if args.parameters:
        parameters = json.loads(args.parameters.read_text(encoding="utf-8"))
    try:
        report = AutomationRunner().run(args.operation_code, targets, parameters)
    except (KeyError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": report["return_code"] == 0, **report}, ensure_ascii=False, indent=2))
    return 0 if report["return_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
