#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def quote(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def jsonb(value):
    return quote(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def render(report):
    staging = report["staging"]
    batch = staging["import_batch"]
    statements = ["BEGIN;"]
    statements.append(
        "INSERT INTO ingest.import_batch ("
        "import_batch_id, file_name, file_sha256, template_version, import_mode, status, actor_ref, summary"
        ") VALUES ("
        f"{quote(batch['import_batch_id'])}, {quote(batch['file_name'])}, {quote(batch['file_sha256'])}, "
        f"{quote(batch['template_version'])}, {quote(batch['import_mode'])}, {quote(batch['status'])}, "
        f"'local-preview', {jsonb(batch['summary'])}"
        ");"
    )
    for row in staging["import_rows"]:
        statements.append(
            "INSERT INTO ingest.import_row ("
            "import_row_id, import_batch_id, sheet_name, row_number, target_object_type, "
            "target_object_id, expected_row_version, raw_payload, normalized_payload, status"
            ") VALUES ("
            f"{quote(row['import_row_id'])}, {quote(row['import_batch_id'])}, {quote(row['sheet_name'])}, "
            f"{int(row['row_number'])}, {quote(row['sheet_name'])}, {quote(row.get('target_object_id'))}, "
            f"{quote(row.get('expected_row_version'))}, {jsonb(row['normalized_payload'])}, "
            f"{jsonb(row['normalized_payload'])}, {quote(row['status'])}"
            ");"
        )
    for item in staging["import_issues"]:
        statements.append(
            "INSERT INTO ingest.import_issue ("
            "import_issue_id, import_batch_id, import_row_id, severity, issue_code, field_path, message"
            ") VALUES ("
            f"{quote(item['import_issue_id'])}, {quote(item['import_batch_id'])}, {quote(item.get('import_row_id'))}, "
            f"{quote(item['severity'])}, {quote(item['code'])}, {quote(item.get('field'))}, {quote(item['message'])}"
            ");"
        )
    for item in staging["import_changes"]:
        statements.append(
            "INSERT INTO ingest.import_change ("
            "import_change_id, import_row_id, field_path, action, before_value, after_value, selected"
            ") VALUES ("
            f"{quote(item['import_change_id'])}, {quote(item['import_row_id'])}, {quote(item['field'])}, "
            f"{quote(item['action'])}, {jsonb(item.get('before'))}, {jsonb(item.get('after'))}, false"
            ");"
        )
    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Render a local import preview as PostgreSQL staging INSERTs.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.input.read_text())
    sql = render(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sql)
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    sys.exit(main())
