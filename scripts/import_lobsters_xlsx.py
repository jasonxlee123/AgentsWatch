#!/usr/bin/env python3
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

HEADER_MAP = {
    "龙虾名称": "lobster_name",
    "飞书应用名称": "lark_app_name",
    "状态": "status",
    "归属部门": "department",
    "智能体类型": "agent_type",
    "智能体版本": "openclaw_version",
    "定向用户": "target_user",
    "定向部门": "target_department",
    "IT负责人": "it_owner",
    "业务负责人": "business_owner",
    "部署位置": "deploy_location",
    "内网IP": "host",
    "服务器ID": "server_id",
    "实例名": "instance_name",
    "端口": "port",
    "内网访问需求（系统：IP/域名：端口）": "network_requirements",
    "交互偏向": "interaction_mode",
    "服务模式": "service_mode",
    "挂载智能体数量": "mounted_agents",
}


def column_index(cell_ref: str) -> int:
    letters = re.match(r"^[A-Z]+", cell_ref or "")
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def text_of(node: ET.Element) -> str:
    return "".join(part.text or "" for part in node.findall(".//a:t", NS)).strip()


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [text_of(item) for item in root.findall("a:si", NS)]


def first_sheet_path(zf: zipfile.ZipFile, sheet_name: str | None) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("rel:Relationship", NS)}
    sheets = workbook.findall("a:sheets/a:sheet", NS)
    selected = None
    for sheet in sheets:
        if sheet_name is None or sheet.attrib.get("name") == sheet_name:
            selected = sheet
            break
    if selected is None:
        names = ", ".join(sheet.attrib.get("name", "") for sheet in sheets)
        raise ValueError(f"sheet not found: {sheet_name}. Available sheets: {names}")
    rel_id = selected.attrib[f"{{{NS['r']}}}id"]
    target = rel_map[rel_id]
    if target.startswith("/"):
        target = target.lstrip("/")
    elif not target.startswith("xl/"):
        target = f"xl/{target}"
    return target


def cell_value(cell: ET.Element, strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return text_of(cell)
    value_node = cell.find("a:v", NS)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    return raw.strip()


def read_rows(path: Path, sheet_name: str | None) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        strings = shared_strings(zf)
        sheet = ET.fromstring(zf.read(first_sheet_path(zf, sheet_name)))
    rows: list[list[str]] = []
    for row in sheet.findall("a:sheetData/a:row", NS):
        values: list[str] = []
        for cell in row.findall("a:c", NS):
            idx = column_index(cell.attrib.get("r", ""))
            while len(values) <= idx:
                values.append("")
            values[idx] = cell_value(cell, strings)
        rows.append(values)
    return rows


def clean(value: object) -> str:
    return str(value or "").strip()


def number(value: str, default: int) -> int:
    try:
        return int(float(clean(value)))
    except ValueError:
        return default


def gateway_id(row: dict) -> str:
    source = clean(row.get("server_id")) or clean(row.get("host")) or clean(row.get("lobster_name"))
    value = re.sub(r"[^a-z0-9._-]+", "-", source.lower()).strip("-")
    if not value:
        raise ValueError(f"cannot derive gateway_id from row: {row}")
    return value


def deployment_mode(instance_name: str) -> str:
    text = clean(instance_name).lower()
    if any(marker in text for marker in ("container", "docker", "容器", "k8s", "kubernetes")):
        return "container"
    return "baremetal"


def enabled(status: str) -> bool:
    return clean(status) in ("", "使用中", "启用", "active", "enabled")


def parse_network_requirements(text: str) -> list[dict]:
    requirements = []
    for line in clean(text).splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[：:]", line)
        if len(parts) >= 3:
            requirements.append({"name": parts[0], "target": parts[1], "port": parts[2]})
        else:
            requirements.append({"raw": line})
    return requirements


def build_inventory(rows: list[list[str]], args: argparse.Namespace) -> dict:
    if not rows:
        raise ValueError("workbook has no rows")
    headers = [HEADER_MAP.get(clean(header), clean(header)) for header in rows[0]]
    gateways = []
    seen = set()
    for raw in rows[1:]:
        if not any(clean(value) for value in raw):
            continue
        item = {headers[idx]: clean(value) for idx, value in enumerate(raw) if idx < len(headers)}
        gid = gateway_id(item)
        if gid in seen:
            raise ValueError(f"duplicate gateway_id: {gid}")
        seen.add(gid)
        mode = deployment_mode(item.get("instance_name", ""))
        gateway = {
            "gateway_id": gid,
            "host": clean(item.get("host")),
            "port": number(item.get("port", ""), args.port),
            "enabled": enabled(item.get("status", "")),
            "deployment_mode": mode,
            "service_mode": clean(item.get("service_mode")) or "single",
            "lobster_name": clean(item.get("lobster_name")),
            "department": clean(item.get("department")),
            "metadata": {
                "lark_app_name": clean(item.get("lark_app_name")),
                "status": clean(item.get("status")),
                "agent_type": clean(item.get("agent_type")),
                "openclaw_version": clean(item.get("openclaw_version")),
                "target_user": clean(item.get("target_user")),
                "target_department": clean(item.get("target_department")),
                "it_owner": clean(item.get("it_owner")),
                "business_owner": clean(item.get("business_owner")),
                "deploy_location": clean(item.get("deploy_location")),
                "server_id": clean(item.get("server_id")),
                "instance_name": clean(item.get("instance_name")),
                "interaction_mode": clean(item.get("interaction_mode")),
                "mounted_agents": number(item.get("mounted_agents", ""), 1),
                "network_requirements": parse_network_requirements(item.get("network_requirements", "")),
            },
        }
        if mode == "container" and clean(item.get("instance_name")):
            gateway["container_name"] = clean(item["instance_name"])
        gateways.append(gateway)
    return {
        "defaults": {
            "cluster": args.cluster,
            "env": args.env,
            "region": args.region,
            "team": args.team,
            "scheme": args.scheme,
            "port": args.port,
            "metrics_path": args.metrics_path,
            "otel_endpoint": args.otel_endpoint,
            "sample_rate": args.sample_rate,
            "flush_interval_ms": args.flush_interval_ms,
            "stuck_session_warn_ms": args.stuck_session_warn_ms,
            "stuck_session_abort_ms": args.stuck_session_abort_ms,
        },
        "gateways": gateways,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import lobster gateway inventory from the first sheet of an xlsx workbook.")
    parser.add_argument("--input", required=True, help="Input .xlsx path")
    parser.add_argument("--output", default="cluster/lobsters.json", help="Output inventory JSON path")
    parser.add_argument("--sheet", help="Worksheet name. Defaults to the first sheet.")
    parser.add_argument("--cluster", default="lobster-prod")
    parser.add_argument("--env", default="prod")
    parser.add_argument("--region", default="cn-beijing")
    parser.add_argument("--team", default="core")
    parser.add_argument("--scheme", default="http")
    parser.add_argument("--port", type=int, default=16402)
    parser.add_argument("--metrics-path", default="/api/diagnostics/prometheus")
    parser.add_argument("--otel-endpoint", default="http://observability.internal:4318")
    parser.add_argument("--sample-rate", type=float, default=1.0)
    parser.add_argument("--flush-interval-ms", type=int, default=60000)
    parser.add_argument("--stuck-session-warn-ms", type=int, default=120000)
    parser.add_argument("--stuck-session-abort-ms", type=int, default=360000)
    args = parser.parse_args()

    inventory = build_inventory(read_rows(Path(args.input), args.sheet), args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n")
    print(f"imported {len(inventory['gateways'])} gateways into {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
