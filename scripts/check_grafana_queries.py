#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


SUBSTITUTIONS = {
    "$__rate_interval": "5m",
    "$__range": "24h",
    "$__interval": "5m",
    "$cluster": ".*",
    "$gateway_id": ".*",
    "$department": ".*",
    "$region": ".*",
    "$team": ".*",
    "$provider": ".*",
    "$model": ".*",
}


def dashboard_panels(path: Path):
    dashboard = json.loads(path.read_text())
    for panel in dashboard.get("panels", []):
        yield panel


def join_field(panel: dict) -> str | None:
    for transformation in panel.get("transformations", []):
        if transformation.get("id") == "joinByField":
            return transformation.get("options", {}).get("byField")
    return None


def test_query(prometheus_url: str, expr: str) -> tuple[str | None, set[str]]:
    rendered = expr
    for variable, value in SUBSTITUTIONS.items():
        rendered = rendered.replace(variable, value)
    url = f"{prometheus_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode({'query': rendered})}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:
        return str(exc), set()
    if payload.get("status") != "success":
        return payload.get("error") or json.dumps(payload), set()
    labels = set()
    for result in payload.get("data", {}).get("result", []):
        labels.update(result.get("metric", {}))
    return None, labels


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated Grafana PromQL queries against Prometheus.")
    parser.add_argument("--dashboard-dir", type=Path, default=Path("grafana/dashboards"))
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    args = parser.parse_args()

    failures = []
    count = 0
    for path in sorted(args.dashboard_dir.glob("*.json")):
        for panel in dashboard_panels(path):
            panel_title = panel.get("title", str(panel.get("id", "?")))
            by_field = join_field(panel)
            label_owners = {}
            duplicate_labels = set()
            for target in panel.get("targets", []):
                expr = target.get("expr")
                if not expr:
                    continue
                count += 1
                ref_id = target.get("refId", "?")
                error, labels = test_query(args.prometheus_url, expr)
                if error:
                    failures.append((path.name, panel_title, ref_id, error))
                    continue
                if not by_field:
                    continue
                for label in labels - {by_field, "__name__"}:
                    owner = label_owners.setdefault(label, ref_id)
                    if owner != ref_id:
                        duplicate_labels.add((label, owner, ref_id))
            for label, first_ref, second_ref in sorted(duplicate_labels):
                failures.append(
                    (
                        path.name,
                        panel_title,
                        second_ref,
                        f'join field "{by_field}" repeats label "{label}" in {first_ref} and {second_ref}',
                    )
                )

    if failures:
        for filename, panel, ref_id, error in failures:
            print(f"FAIL {filename} :: {panel} [{ref_id}] :: {error}", file=sys.stderr)
        print(f"{len(failures)} of {count} queries failed", file=sys.stderr)
        return 1

    print(f"validated {count} Grafana queries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
