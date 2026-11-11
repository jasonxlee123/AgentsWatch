#!/usr/bin/env python3
from collections import Counter


CLEAR_MARKER = "__CLEAR__"


def import_issue(code, field, message):
    return {"code": code, "field": field, "message": message}


def plan_import_batch(
    current_objects,
    rows,
    *,
    mutable_fields,
    nullable_fields=(),
    import_mode="standard",
    require_external_for_create=True,
):
    current_by_id = {item["platform_id"]: item for item in current_objects}
    current_by_external = {
        item["external_code"]: item
        for item in current_objects
        if item.get("external_code") not in (None, "")
    }
    nullable_fields = set(nullable_fields)
    mutable_fields = tuple(mutable_fields)

    provided_ids = Counter(row.get("platform_id") for row in rows if row.get("platform_id"))
    provided_external = Counter(
        row.get("external_code")
        for row in rows
        if not row.get("platform_id") and row.get("external_code")
    )

    results = []
    for row_number, row in enumerate(rows, start=1):
        issues = []
        changes = []
        platform_id = row.get("platform_id")
        external_code = row.get("external_code")

        if platform_id and provided_ids[platform_id] > 1:
            issues.append(import_issue("DUPLICATE_IMPORT_ROW", "platform_id", "platform ID occurs more than once in this batch"))
        if not platform_id and external_code and provided_external[external_code] > 1:
            issues.append(import_issue("DUPLICATE_IMPORT_ROW", "external_code", "external code occurs more than once in this batch"))

        current = None
        if platform_id:
            current = current_by_id.get(platform_id)
            if current is None:
                issues.append(import_issue("REFERENCE_NOT_FOUND", "platform_id", "platform object does not exist"))
            elif external_code and current.get("external_code") != external_code:
                issues.append(import_issue("IDENTIFIER_TARGET_MISMATCH", "external_code", "UUID and external code identify different objects"))
        elif external_code and external_code in current_by_external:
            if import_mode == "initial_migration":
                current = current_by_external[external_code]
                platform_id = current["platform_id"]
            else:
                issues.append(import_issue("PLATFORM_ID_REQUIRED_FOR_UPDATE", "platform_id", "existing object updates require platform UUID"))

        if current and row.get("row_version") != current.get("row_version"):
            issues.append(import_issue("ROW_VERSION_CONFLICT", "row_version", "master data changed after export or staging"))

        if require_external_for_create and not current and not platform_id and not external_code:
            issues.append(import_issue("EXTERNAL_IDENTIFIER_REQUIRED_FOR_CREATE", "external_code", "new object requires a stable external code"))

        if issues:
            results.append(
                {
                    "row_number": row_number,
                    "status": "conflict" if any(item["code"] == "ROW_VERSION_CONFLICT" for item in issues) else "invalid",
                    "platform_id": platform_id,
                    "changes": [],
                    "issues": issues,
                }
            )
            continue

        for field in mutable_fields:
            incoming = row.get(field)
            if incoming in (None, ""):
                changes.append({"field": field, "action": "ignore"})
                continue
            if incoming == CLEAR_MARKER:
                if field not in nullable_fields:
                    issues.append(import_issue("FIELD_NOT_CLEARABLE", field, "field cannot be cleared"))
                elif current and current.get(field) is not None:
                    changes.append({"field": field, "action": "clear", "before": current.get(field), "after": None})
                else:
                    changes.append({"field": field, "action": "ignore"})
                continue
            before = current.get(field) if current else None
            if current and incoming == before:
                changes.append({"field": field, "action": "ignore"})
            else:
                changes.append({"field": field, "action": "set", "before": before, "after": incoming})

        if issues:
            status = "invalid"
            changes = []
        elif current is None:
            status = "new"
        elif any(item["action"] in {"set", "clear"} for item in changes):
            status = "changed"
        else:
            status = "unchanged"
        results.append(
            {
                "row_number": row_number,
                "status": status,
                "platform_id": platform_id,
                "changes": changes,
                "issues": issues,
            }
        )

    return {
        "ready": all(not result["issues"] for result in results),
        "rows": results,
        "summary": dict(sorted(Counter(result["status"] for result in results).items())),
    }
