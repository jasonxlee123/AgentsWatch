#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Run the local AgentsWatch management API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--database-url", default=os.environ.get("AGENTSWATCH_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or AGENTSWATCH_DATABASE_URL is required")
    os.environ["AGENTSWATCH_DATABASE_URL"] = args.database_url
    from agentswatch_api.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
