#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import re
import sys
import unicodedata
import uuid
import zipfile
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from simulate_asset_import_contract import CLEAR_MARKER, plan_import_batch


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "asset_import_template_v2.json"
CELL_REF = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
SECRET_PATTERN = re.compile(
    r"(?i)(password\s*[=:]|token\s*[=:]|private[ _-]?key|cookie\s*[=:]|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[a-z0-9_-]{12,})"
)
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
IMPORT_NAMESPACE = uuid.UUID("0d62f890-6f54-46a1-86d2-18d3302d5087")


def column_index(cell_ref):
    match = CELL_REF.match(cell_ref)
    if not match:
        raise ValueError(f"invalid cell reference {cell_ref!r}")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - 64
    return result - 1, int(match.group(2)) - 1


def text_content(element):
    return "".join(node.text or "" for node in element.iter() if node.tag.endswith("}t"))


def read_shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [text_content(item) for item in root.findall(f"{{{MAIN_NS}}}si")]


def read_sheet(archive, target, shared_strings):
    root = ET.fromstring(archive.read(target))
    rows = {}
    formulas = set()
    for cell in root.findall(f".//{{{MAIN_NS}}}c"):
        ref = cell.attrib.get("r")
        if not ref:
            continue
        column, row = column_index(ref)
        formula = cell.find(f"{{{MAIN_NS}}}f")
        if formula is not None:
            formulas.add((row, column))
        cell_type = cell.attrib.get("t")
        value_node = cell.find(f"{{{MAIN_NS}}}v")
        if cell_type == "inlineStr":
            inline = cell.find(f"{{{MAIN_NS}}}is")
            value = text_content(inline) if inline is not None else ""
        elif value_node is None:
            value = None
        elif cell_type == "s":
            value = shared_strings[int(value_node.text)]
        elif cell_type == "b":
            value = value_node.text == "1"
        elif cell_type in {"str", "e"}:
            value = value_node.text
        else:
            raw = value_node.text
            try:
                number = Decimal(raw)
                value = int(number) if number == number.to_integral_value() else float(number)
            except (InvalidOperation, TypeError):
                value = raw
        rows.setdefault(row, {})[column] = value
    if not rows:
        return [], formulas
    width = max((max(values) for values in rows.values() if values), default=-1) + 1
    matrix = []
    for row_index in range(max(rows) + 1):
        values = rows.get(row_index, {})
        matrix.append([values.get(column) for column in range(width)])
    return matrix, formulas


def read_xlsx(path):
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("file is not a valid unencrypted .xlsx archive") from exc
    with archive:
        names = set(archive.namelist())
        forbidden = [name for name in names if "vbaProject.bin" in name or name.startswith("xl/externalLinks/")]
        if forbidden:
            raise ValueError(f"workbook contains forbidden macro or external link parts: {sorted(forbidden)}")
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationships = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        shared_strings = read_shared_strings(archive)
        sheets = {}
        for sheet in workbook_root.findall(f".//{{{MAIN_NS}}}sheet"):
            name = sheet.attrib["name"]
            relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = relationships[relationship_id]
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = f"xl/{target}"
            rows, formulas = read_sheet(archive, target, shared_strings)
            sheets[name] = {"rows": rows, "formulas": formulas}
        return sheets


def normalized_text(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return unicodedata.normalize("NFC", str(value).strip())


def is_empty(value):
    return value is None or value == ""


def issue(code, field, message, severity="error"):
    return {"severity": severity, "code": code, "field": field, "message": message}


def normalize_version(value):
    try:
        return Decimal(str(value)).normalize()
    except InvalidOperation:
        return str(value).strip()


def validate_type(field, value, field_type, enums):
    if is_empty(value) or value == CLEAR_MARKER:
        return None
    try:
        if field_type == "uuid":
            parsed = uuid.UUID(str(value))
            if str(parsed) != str(value):
                return issue("UUID_FORMAT_INVALID", field, "UUID must use lowercase canonical hyphenated form")
        elif field_type == "integer":
            if isinstance(value, bool) or Decimal(str(value)) != Decimal(str(value)).to_integral_value():
                raise ValueError
        elif field_type == "positive_integer":
            number = Decimal(str(value))
            if number != number.to_integral_value() or number <= 0:
                raise ValueError
        elif field_type == "positive_decimal":
            if Decimal(str(value)) <= 0:
                raise ValueError
        elif field_type == "boolean":
            if value not in (True, False, "true", "false"):
                raise ValueError
        elif field_type == "port":
            number = Decimal(str(value))
            if number != number.to_integral_value() or not 1 <= number <= 65535:
                raise ValueError
        elif field_type == "ip":
            ipaddress.ip_address(str(value))
        elif field_type == "timestamp":
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return issue("TIMESTAMP_TIMEZONE_REQUIRED", field, "timestamp must include a timezone")
        elif field_type == "enum" and value not in enums.get(field, []):
            return issue("ENUM_VALUE_INVALID", field, f"value must be one of {enums.get(field, [])}")
    except (ValueError, InvalidOperation):
        return issue("FIELD_TYPE_INVALID", field, f"value is invalid for type {field_type}")
    return None


def validate_conditional_rules(sheet_name, row):
    issues = []
    if sheet_name == "Agents":
        role = row.get("structure_role")
        archetype = row.get("service_archetype")
        lifecycle = row.get("lifecycle_status")
        if role == "primary":
            if not is_empty(row.get("parent_agent_platform_id")):
                issues.append(issue("PRIMARY_AGENT_HAS_PARENT", "parent_agent_platform_id", "Primary Agent cannot have a parent"))
            if lifecycle in {"testing", "active"}:
                for field in ("business_admin_employee_no", "it_admin_employee_no"):
                    if is_empty(row.get(field)):
                        issues.append(issue("ACTIVE_AGENT_ADMIN_REQUIRED", field, "testing/active Primary requires this administrator"))
            rules = {
                "personal": ("primary_service_employee_no", ["primary_service_department_code", "business_domain", "business_scenario"]),
                "department": ("primary_service_department_code", ["primary_service_employee_no", "business_domain", "business_scenario"]),
                "business": ("business_domain", ["primary_service_employee_no", "primary_service_department_code"]),
            }
            required, forbidden = rules.get(archetype, (None, []))
            if required and is_empty(row.get(required)):
                issues.append(issue("SERVICE_ARCHETYPE_FIELD_REQUIRED", required, f"{archetype} Primary requires this field"))
            if archetype == "business" and is_empty(row.get("business_scenario")):
                issues.append(issue("SERVICE_ARCHETYPE_FIELD_REQUIRED", "business_scenario", "business Primary requires this field"))
            for field in forbidden:
                if not is_empty(row.get(field)):
                    issues.append(issue("INVALID_SERVICE_ARCHETYPE_FIELDS", field, f"field is not allowed for {archetype} Primary"))
        elif role == "subagent":
            if is_empty(row.get("parent_agent_platform_id")):
                issues.append(issue("INVALID_AGENT_HIERARCHY", "parent_agent_platform_id", "Subagent requires a Primary parent"))
            for field in (
                "business_admin_employee_no", "it_admin_employee_no", "primary_service_employee_no",
                "primary_service_department_code", "business_domain", "business_scenario",
            ):
                if not is_empty(row.get(field)):
                    issues.append(issue("SUBAGENT_FIELD_MUST_INHERIT", field, "Subagent must inherit this field"))
    elif sheet_name == "Runtimes":
        mode = row.get("deployment_mode")
        if mode == "container" and (is_empty(row.get("container_name")) or is_empty(row.get("image_ref"))):
            issues.append(issue("RUNTIME_MODE_FIELDS_INVALID", "deployment_mode", "container Runtime requires container_name and image_ref"))
        if mode == "baremetal" and (not is_empty(row.get("container_name")) or not is_empty(row.get("image_ref"))):
            issues.append(issue("RUNTIME_MODE_FIELDS_INVALID", "deployment_mode", "baremetal Runtime cannot contain container fields"))
    elif sheet_name in {"ServerGroupMembers", "AgentAccessUsers", "AgentGroupMembers", "AgentDeployments", "NetworkRequirements"}:
        reference_pairs = {
            "ServerGroupMembers": ("server_platform_id", "provider_instance_id"),
            "AgentAccessUsers": ("agent_platform_id", "agent_external_code"),
            "AgentGroupMembers": ("agent_platform_id", "agent_external_code"),
            "AgentDeployments": ("primary_agent_platform_id", "agent_external_code"),
            "NetworkRequirements": ("agent_platform_id", "agent_external_code"),
        }
        left, right = reference_pairs[sheet_name]
        if is_empty(row.get(left)) and is_empty(row.get(right)):
            issues.append(issue("REFERENCE_REQUIRED", left, f"provide {left} or {right}"))
        if sheet_name == "AgentDeployments" and is_empty(row.get("runtime_platform_id")) and is_empty(row.get("legacy_runtime_id")):
            issues.append(issue("REFERENCE_REQUIRED", "runtime_platform_id", "provide runtime_platform_id or legacy_runtime_id"))
    elif sheet_name == "AgentDeploymentMembers":
        if row.get("member_role") == "subagent" and is_empty(row.get("parent_member_platform_id")):
            issues.append(issue("DEPLOYMENT_SUBAGENT_PARENT_INVALID", "parent_member_platform_id", "Subagent member requires parent member"))
        if row.get("member_role") == "primary" and not is_empty(row.get("parent_member_platform_id")):
            issues.append(issue("DEPLOYMENT_PRIMARY_MEMBER_INVALID", "parent_member_platform_id", "Primary member cannot have parent member"))
        if row.get("mapping_status") == "verified" and is_empty(row.get("runtime_agent_key")):
            issues.append(issue("RUNTIME_AGENT_KEY_REQUIRED", "runtime_agent_key", "verified mapping requires runtime_agent_key"))
    return issues


def rows_from_matrix(sheet, definition):
    matrix = sheet["rows"]
    expected_headers = definition["headers"]
    if not matrix:
        return None, [issue("SHEET_HEADER_MISSING", None, "sheet is empty")]
    actual_headers = [normalized_text(value) for value in matrix[0][: len(expected_headers)]]
    if actual_headers != expected_headers or any(not is_empty(value) for value in matrix[0][len(expected_headers) :]):
        return None, [issue("SHEET_HEADER_MISMATCH", None, f"expected headers {expected_headers}, received {actual_headers}")]
    result = []
    for row_index, values in enumerate(matrix[1:], start=2):
        padded = values + [None] * (len(expected_headers) - len(values))
        normalized = {header: normalized_text(padded[index]) for index, header in enumerate(expected_headers)}
        if all(is_empty(value) for value in normalized.values()):
            continue
        formula_fields = {
            expected_headers[column]
            for formula_row, column in sheet["formulas"]
            if formula_row == row_index - 1 and column < len(expected_headers)
        }
        result.append({"row_number": row_index, "values": normalized, "formula_fields": formula_fields})
    return result, []


def validate_and_plan_sheet(definition, parsed_rows, master_rows, import_mode):
    row_reports = []
    valid_rows = []
    valid_row_positions = []
    external_key = definition.get("external_key")
    duplicate_fields = ["platform_id"] + ([external_key] if external_key else [])
    duplicate_counts = {
        field: Counter(
            row["values"].get(field)
            for row in parsed_rows
            if not is_empty(row["values"].get(field))
        )
        for field in duplicate_fields
    }

    for parsed in parsed_rows:
        row = dict(parsed["values"])
        issues = []
        for field in parsed["formula_fields"] & set(definition.get("identifier_fields", [])):
            issues.append(issue("IDENTIFIER_FORMULA_FORBIDDEN", field, "identifier fields cannot contain formulas"))
        for field in duplicate_fields:
            value = row.get(field)
            if not is_empty(value) and duplicate_counts[field][value] > 1:
                issues.append(issue("DUPLICATE_IMPORT_ROW", field, "value occurs more than once in this Sheet"))
        for field, value in list(row.items()):
            if definition.get("types", {}).get(field) == "timestamp" and isinstance(value, str) and value.startswith("'"):
                value = value[1:]
                row[field] = value
            if isinstance(value, str) and SECRET_PATTERN.search(value):
                issues.append(issue("SECRET_CONTENT_DETECTED", field, "suspected secret content is forbidden"))
                row[field] = "[REDACTED]"
            if value == CLEAR_MARKER and field not in definition.get("nullable", []):
                issues.append(issue("FIELD_NOT_CLEARABLE", field, "field is not nullable"))
            type_issue = validate_type(field, value, definition.get("types", {}).get(field), definition.get("enums", {}))
            if type_issue:
                issues.append(type_issue)
        if is_empty(row.get("platform_id")):
            for field in definition.get("required_on_create", []):
                if is_empty(row.get(field)) or row.get(field) == CLEAR_MARKER:
                    issues.append(issue("REQUIRED_FIELD_MISSING", field, "field is required for a new object"))
        elif is_empty(row.get("row_version")):
            issues.append(issue("ROW_VERSION_REQUIRED", "row_version", "updates require row_version"))
        issues.extend(validate_conditional_rules(definition["name"], row))
        if issues:
            row_reports.append({
                "row_number": parsed["row_number"], "status": "invalid", "normalized_payload": row,
                "changes": [], "issues": issues,
            })
        else:
            generic = dict(row)
            if external_key:
                generic["external_code"] = row.get(external_key)
            valid_rows.append(generic)
            valid_row_positions.append((parsed["row_number"], row))

    if valid_rows:
        generic_master = []
        for current in master_rows:
            item = dict(current)
            if external_key:
                item["external_code"] = current.get(external_key)
            generic_master.append(item)
        mutable = [field for field in definition["headers"] if field not in {"platform_id", "row_version"}]
        planned = plan_import_batch(
            generic_master,
            valid_rows,
            mutable_fields=mutable,
            nullable_fields=definition.get("nullable", []),
            import_mode=import_mode,
            require_external_for_create=False,
        )
        for (row_number, normalized), planned_row in zip(valid_row_positions, planned["rows"]):
            row_reports.append({
                "row_number": row_number,
                "status": planned_row["status"],
                "platform_id": planned_row.get("platform_id"),
                "normalized_payload": normalized,
                "changes": planned_row["changes"],
                "issues": [dict(item, severity="error") for item in planned_row["issues"]],
            })
    row_reports.sort(key=lambda item: item["row_number"])
    return row_reports


def preview_workbook(input_path, config_path=DEFAULT_CONFIG, master=None, import_mode="standard"):
    config = json.loads(Path(config_path).read_text())
    sheets = read_xlsx(input_path)
    issues = []
    meta_sheet = sheets.get("_Meta")
    meta_values = {}
    if meta_sheet and meta_sheet["rows"]:
        for row in meta_sheet["rows"][1:]:
            if len(row) >= 2 and not is_empty(row[0]):
                meta_values[str(row[0]).strip()] = row[1]
    else:
        issues.append(issue("TEMPLATE_METADATA_MISSING", "_Meta", "_Meta Sheet is required"))
    actual_version = meta_values.get("agentswatch_template_version")
    if normalize_version(actual_version) != normalize_version(config["template_version"]):
        issues.append(issue("TEMPLATE_VERSION_UNSUPPORTED", "_Meta.agentswatch_template_version", f"expected {config['template_version']}, received {actual_version}"))

    master = master or {}
    sheet_reports = []
    all_rows = []
    expected_sheet_names = [definition["name"] for definition in config["sheets"]]
    for definition in config["sheets"]:
        sheet = sheets.get(definition["name"])
        if sheet is None:
            issues.append(issue("REQUIRED_SHEET_MISSING", definition["name"], "required Sheet is missing"))
            sheet_reports.append({"sheet": definition["name"], "rows": 0, "status_counts": {}})
            continue
        parsed_rows, header_issues = rows_from_matrix(sheet, definition)
        if header_issues:
            issues.extend(dict(item, field=f"{definition['name']}.{item['field'] or 'header'}") for item in header_issues)
            sheet_reports.append({"sheet": definition["name"], "rows": 0, "status_counts": {"invalid": 1}})
            continue
        reports = validate_and_plan_sheet(definition, parsed_rows, master.get(definition["name"], []), import_mode)
        for report in reports:
            report["sheet"] = definition["name"]
        all_rows.extend(reports)
        sheet_reports.append({
            "sheet": definition["name"],
            "rows": len(reports),
            "status_counts": dict(sorted(Counter(item["status"] for item in reports).items())),
        })
    unexpected = sorted(set(sheets) - set(expected_sheet_names) - {"Instructions", "_Meta", "_Enums"})
    for name in unexpected:
        issues.append(issue("UNEXPECTED_SHEET", name, "Sheet is ignored", severity="warning"))
    error_count = sum(item["severity"] == "error" for item in issues)
    error_count += sum(
        item["severity"] == "error"
        for row in all_rows
        for item in row["issues"]
    )
    warning_count = sum(item["severity"] == "warning" for item in issues)
    warning_count += sum(
        item["severity"] == "warning"
        for row in all_rows
        for item in row["issues"]
    )
    source_bytes = Path(input_path).read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    batch_id = str(uuid.uuid5(IMPORT_NAMESPACE, f"{source_hash}:{import_mode}"))
    import_rows = []
    import_issues = []
    import_changes = []
    for row in all_rows:
        import_row_id = str(uuid.uuid5(IMPORT_NAMESPACE, f"{batch_id}:{row['sheet']}:{row['row_number']}"))
        import_rows.append(
            {
                "import_row_id": import_row_id,
                "import_batch_id": batch_id,
                "sheet_name": row["sheet"],
                "row_number": row["row_number"],
                "target_object_id": row.get("platform_id"),
                "expected_row_version": row["normalized_payload"].get("row_version"),
                "normalized_payload": row["normalized_payload"],
                "status": row["status"],
            }
        )
        for position, row_issue in enumerate(row["issues"]):
            import_issues.append(
                {
                    "import_issue_id": str(uuid.uuid5(IMPORT_NAMESPACE, f"{import_row_id}:issue:{position}")),
                    "import_batch_id": batch_id,
                    "import_row_id": import_row_id,
                    **row_issue,
                }
            )
        for position, change in enumerate(row["changes"]):
            import_changes.append(
                {
                    "import_change_id": str(uuid.uuid5(IMPORT_NAMESPACE, f"{import_row_id}:change:{position}")),
                    "import_row_id": import_row_id,
                    **change,
                }
            )
    report = {
        "ready": error_count == 0,
        "template_version": config["template_version"],
        "source_file": Path(input_path).name,
        "source_sha256": source_hash,
        "import_mode": import_mode,
        "summary": {
            "sheets": len(config["sheets"]),
            "rows": len(all_rows),
            "status_counts": dict(sorted(Counter(item["status"] for item in all_rows).items())),
            "errors": error_count,
            "warnings": warning_count,
        },
        "sheets": sheet_reports,
        "rows": all_rows,
        "issues": issues,
    }
    report["staging"] = {
        "import_batch": {
            "import_batch_id": batch_id,
            "file_name": report["source_file"],
            "file_sha256": source_hash,
            "template_version": config["template_version"],
            "import_mode": import_mode,
            "status": "ready" if report["ready"] else "rejected",
            "summary": report["summary"],
        },
        "import_rows": import_rows,
        "import_issues": import_issues,
        "import_changes": import_changes,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Parse and preview an AgentsWatch core asset .xlsx import locally.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--import-mode", choices=("standard", "initial_migration"), default="standard")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.input.suffix.lower() != ".xlsx":
        parser.error("--input must be an .xlsx file")
    master = json.loads(args.master.read_text()) if args.master else None
    report = preview_workbook(args.input, args.config, master, args.import_mode)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
