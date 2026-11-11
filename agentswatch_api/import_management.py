import hashlib
import ipaddress
import zipfile
from collections import defaultdict
from io import BytesIO
from uuid import UUID, uuid4

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from psycopg.types.json import Jsonb

from .database import WriteDatabase
from .runtime_management import (
    active_runtime_deployment_state,
    unmanaged_runtime_metadata_update_allowed,
)
from .server_management import ConflictError, NotFoundError


TEMPLATE_VERSION = "bulk-import-v1"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DATA_ROWS = 2000
SOURCE_SYSTEM = "bulk_import"

SHEET_SPECS = {
    "部门": {
        "object_type": "department",
        "key": "department_code",
        "headers": ["department_code", "parent_department_code", "name", "is_active"],
        "required": {"department_code", "name"},
        "defaults": {"is_active": True},
    },
    "人员": {
        "object_type": "person",
        "key": "employee_no",
        "headers": ["employee_no", "display_name", "primary_department_code", "employment_status"],
        "required": {"employee_no", "display_name"},
        "defaults": {"employment_status": "active"},
    },
    "服务器": {
        "object_type": "server",
        "key": "server_code",
        "headers": [
            "server_code", "environment_code", "provider_code", "name", "lifecycle_status",
            "cpu_cores", "memory_gib", "disk_gib", "internal_ip", "public_ip",
            "os_name", "os_version", "architecture", "work_account",
        ],
        "required": {"server_code", "environment_code", "name"},
        "defaults": {"lifecycle_status": "planned"},
    },
    "运行时": {
        "object_type": "runtime",
        "key": "runtime_code",
        "headers": [
            "runtime_code", "server_code", "runtime_family_code", "name", "runtime_role",
            "deployment_mode", "desired_version", "lifecycle_status", "bind_address",
            "endpoint_port", "runtime_account", "config_path", "state_path", "container_name",
            "image_ref",
        ],
        "required": {"runtime_code", "server_code", "runtime_family_code", "name", "deployment_mode"},
        "defaults": {"runtime_role": "gateway", "lifecycle_status": "planned"},
    },
    "智能体": {
        "object_type": "agent",
        "key": "agent_code",
        "headers": [
            "agent_code", "name", "structure_role", "parent_agent_code", "service_archetype",
            "lifecycle_status", "owning_department_code", "business_admin_employee_no",
            "it_admin_employee_no", "primary_service_employee_no", "primary_service_department_code",
            "business_domain", "business_scenario", "description",
        ],
        "required": {"agent_code", "name", "structure_role", "service_archetype"},
        "defaults": {"lifecycle_status": "requested"},
    },
}

OBJECT_SHEETS = {spec["object_type"]: sheet for sheet, spec in SHEET_SPECS.items()}
ENUMS = {
    "employment_status": {"active", "inactive", "departed"},
    "server.lifecycle_status": {"planned", "active", "maintenance", "disabled", "retired"},
    "runtime.runtime_role": {"gateway"},
    "runtime.deployment_mode": {"baremetal", "container"},
    "runtime.lifecycle_status": {"planned", "active", "disabled", "retired"},
    "agent.structure_role": {"primary", "subagent"},
    "agent.service_archetype": {"personal", "department", "business"},
    "agent.lifecycle_status": {"requested", "testing", "active", "suspended", "retired"},
}
NUMERIC_FIELDS = {"cpu_cores", "memory_gib", "disk_gib"}


def _clean_cell(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _boolean(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "是"}:
        return True
    if normalized in {"false", "0", "no", "n", "否"}:
        return False
    raise ValueError("必须填写 true/false（或 是/否）")


def _number(value, integer=False):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("必须是数字") from exc
    if number <= 0:
        raise ValueError("必须大于 0")
    if integer:
        if not number.is_integer():
            raise ValueError("必须是整数")
        return int(number)
    return number


def _topological(rows, key_field, parent_field):
    by_code = {row["normalized_payload"].get(key_field): row for row in rows}
    ordered = []
    visiting = set()
    visited = set()

    def visit(code):
        if code in visited:
            return
        if code in visiting:
            raise ValueError(f"{code} 存在循环父子关系")
        visiting.add(code)
        row = by_code[code]
        parent = row["normalized_payload"].get(parent_field)
        if parent in by_code:
            visit(parent)
        visiting.remove(code)
        visited.add(code)
        ordered.append(row)

    for code in by_code:
        visit(code)
    return ordered


class BulkImportService:
    def __init__(self, database=None):
        self.database = database or WriteDatabase()

    @staticmethod
    def template_bytes():
        workbook = Workbook()
        guide = workbook.active
        guide.title = "说明"
        guide.append(["AgentsWatch 批量导入模板", TEMPLATE_VERSION])
        guide.append(["使用方法", "在需要导入的工作表中逐行填写；未使用的工作表保留表头即可。上传后先预览，确认无错误再整批入库。"])
        guide.append(["匹配规则", "部门按 department_code、人员按 employee_no，其余对象按 *_code 匹配；相同编码会更新，不同编码会新增。"])
        guide.append(["空值规则", "更新已有对象时，空白的可选字段会被清空；文件中缺少某个对象不会删除或停用数据库记录。"])
        guide.append(["事务规则", "五类数据一次性提交；任何校验或并发冲突都会阻止整批写入。"])
        guide.append(["引用规则", "父部门、所属部门、人员、服务器、父智能体均填写对应编码，可引用本文件内同批新增的数据。"])
        guide.append(["部门示例", "DEP-EXAMPLE | （父部门留空） | 示例部门 | true"])
        guide.append(["人员示例", "EMP-EXAMPLE | 示例人员 | DEP-EXAMPLE | active"])
        guide.append(["服务器示例", "SRV-EXAMPLE | prod | （云厂商可空） | 示例服务器 | planned | …"])
        guide.append(["运行时示例", "RT-EXAMPLE | SRV-EXAMPLE | openclaw | 示例运行时 | gateway | baremetal | …"])
        guide.append(["智能体示例", "AGT-EXAMPLE | 示例智能体 | primary | （父智能体留空） | department | requested | …"])
        guide.column_dimensions["A"].width = 18
        guide.column_dimensions["B"].width = 100
        guide["A1"].font = Font(bold=True, color="FFFFFF")
        guide["B1"].font = Font(bold=True, color="FFFFFF")
        guide["A1"].fill = guide["B1"].fill = PatternFill("solid", fgColor="0B8F94")
        guide.freeze_panes = "A2"

        for sheet_name, spec in SHEET_SPECS.items():
            sheet = workbook.create_sheet(sheet_name)
            sheet.append(spec["headers"])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = f"A1:{get_column_letter(len(spec['headers']))}1"
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="0B8F94")
                cell.alignment = Alignment(horizontal="center")
            for index, header in enumerate(spec["headers"], 1):
                width = max(15, min(34, len(header) + 4))
                sheet.column_dimensions[get_column_letter(index)].width = width
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    @staticmethod
    def _secure_workbook(file_name, content):
        if not file_name or not file_name.lower().endswith(".xlsx"):
            raise ValueError("仅支持 .xlsx 文件")
        if not content or len(content) > MAX_FILE_BYTES:
            raise ValueError("文件为空或超过 10 MB")
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Excel 解压内容过大")
                lowered = [item.filename.lower() for item in entries]
                if any("vbaproject.bin" in name or "/externallinks/" in name for name in lowered):
                    raise ValueError("Excel 不允许包含宏或外部链接")
        except zipfile.BadZipFile as exc:
            raise ValueError("文件不是有效的 .xlsx 工作簿") from exc
        try:
            return load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        except Exception as exc:
            raise ValueError("无法读取 Excel 工作簿") from exc

    @classmethod
    def _parse(cls, file_name, content):
        workbook = cls._secure_workbook(file_name, content)
        missing = [name for name in SHEET_SPECS if name not in workbook.sheetnames]
        if missing:
            raise ValueError(f"缺少工作表：{', '.join(missing)}")
        parsed = []
        total = 0
        for sheet_name, spec in SHEET_SPECS.items():
            sheet = workbook[sheet_name]
            rows = sheet.iter_rows(values_only=False)
            header_cells = next(rows, ())
            headers = [_clean_cell(cell.value) for cell in header_cells]
            if headers != spec["headers"]:
                raise ValueError(f"工作表“{sheet_name}”表头不正确，请使用最新模板")
            seen = set()
            for row_number, cells in enumerate(rows, 2):
                values = [_clean_cell(cell.value) for cell in cells[:len(headers)]]
                if not any(value is not None for value in values):
                    continue
                total += 1
                if total > MAX_DATA_ROWS:
                    raise ValueError(f"单批最多导入 {MAX_DATA_ROWS} 行")
                raw = dict(zip(headers, values))
                issues = []
                if any(cell.data_type == "f" for cell in cells):
                    issues.append((None, "FORMULA_NOT_ALLOWED", "不允许使用公式，请粘贴计算后的值"))
                normalized = dict(raw)
                for field, value in normalized.items():
                    if value is not None and field not in NUMERIC_FIELDS | {"endpoint_port", "is_active"}:
                        normalized[field] = str(value).strip() or None
                for field, default in spec.get("defaults", {}).items():
                    if normalized.get(field) is None:
                        normalized[field] = default
                try:
                    if "is_active" in normalized:
                        normalized["is_active"] = _boolean(normalized["is_active"])
                except ValueError as exc:
                    issues.append(("is_active", "INVALID_BOOLEAN", str(exc)))
                for field in NUMERIC_FIELDS:
                    if field in normalized:
                        try:
                            normalized[field] = _number(normalized[field])
                        except ValueError as exc:
                            issues.append((field, "INVALID_NUMBER", str(exc)))
                if "endpoint_port" in normalized:
                    try:
                        normalized["endpoint_port"] = _number(normalized["endpoint_port"], integer=True)
                        if normalized["endpoint_port"] and normalized["endpoint_port"] > 65535:
                            raise ValueError("必须在 1 到 65535 之间")
                    except ValueError as exc:
                        issues.append(("endpoint_port", "INVALID_PORT", str(exc)))
                for field in spec["required"]:
                    if normalized.get(field) is None:
                        issues.append((field, "REQUIRED_FIELD", "必填字段不能为空"))
                key = normalized.get(spec["key"])
                if key is not None:
                    key = str(key).strip()
                    normalized[spec["key"]] = key
                    if key in seen:
                        issues.append((spec["key"], "DUPLICATE_CODE", f"编码 {key} 在当前工作表重复"))
                    seen.add(key)
                object_type = spec["object_type"]
                for field, allowed in ENUMS.items():
                    if "." in field:
                        prefix, field_name = field.split(".", 1)
                        if prefix != object_type:
                            continue
                    else:
                        field_name = field
                    value = normalized.get(field_name)
                    if value is not None and str(value) not in allowed:
                        issues.append((field_name, "INVALID_ENUM", f"可选值：{', '.join(sorted(allowed))}"))
                parsed.append({
                    "sheet_name": sheet_name,
                    "row_number": row_number,
                    "object_type": object_type,
                    "key": key,
                    "raw_payload": raw,
                    "normalized_payload": normalized,
                    "issues": issues,
                })
        workbook.close()
        if not parsed:
            raise ValueError("工作簿中没有可导入的数据")
        return parsed

    @staticmethod
    def _load_state(connection):
        state = {
            "department": {}, "person": {}, "server": {}, "runtime": {}, "agent": {},
            "environment": {}, "provider": {}, "runtime_family": {},
        }
        for row in connection.execute("SELECT environment_id, environment_code FROM asset.environment"):
            state["environment"][row["environment_code"]] = row["environment_id"]
        for row in connection.execute("SELECT cloud_provider_id, provider_code FROM asset.cloud_provider"):
            state["provider"][row["provider_code"]] = row["cloud_provider_id"]
        for row in connection.execute("SELECT runtime_family_id, family_code FROM asset.runtime_family"):
            state["runtime_family"][row["family_code"]] = row["runtime_family_id"]

        for row in connection.execute(
            """
            SELECT d.department_id AS id, d.department_code AS code, d.row_version,
                   d.department_code, parent.department_code AS parent_department_code,
                   d.name, d.is_active
            FROM org.department d
            LEFT JOIN org.department parent ON parent.department_id = d.parent_department_id
            """
        ):
            state["department"][row["code"]] = {"id": row["id"], "row_version": row["row_version"], "payload": {k: row[k] for k in SHEET_SPECS["部门"]["headers"]}}
        for row in connection.execute(
            """
            SELECT p.person_id AS id, p.employee_no AS code, p.row_version, p.employee_no,
                   p.display_name, d.department_code AS primary_department_code,
                   p.employment_status
            FROM org.person p LEFT JOIN org.department d ON d.department_id = p.primary_department_id
            """
        ):
            state["person"][row["code"]] = {"id": row["id"], "row_version": row["row_version"], "payload": {k: row[k] for k in SHEET_SPECS["人员"]["headers"]}}

        server_rows = connection.execute(
            """
            SELECT s.server_id AS id, COALESCE(code.identifier_value, s.provider_instance_id) AS code,
                   s.row_version, e.environment_code, cp.provider_code, s.name,
                   s.lifecycle_status::text AS lifecycle_status, s.cpu_cores::double precision AS cpu_cores,
                   s.memory_gib::double precision AS memory_gib, s.disk_gib::double precision AS disk_gib,
                   host(s.internal_ip) AS internal_ip, host(s.public_ip) AS public_ip,
                   s.os_name, s.os_version, s.architecture, s.work_account
            FROM asset.server_asset s
            JOIN asset.environment e ON e.environment_id = s.environment_id
            LEFT JOIN asset.cloud_provider cp ON cp.cloud_provider_id = s.cloud_provider_id
            LEFT JOIN LATERAL (
                SELECT identifier_value FROM asset.external_identifier
                WHERE server_id = s.server_id AND source_system = 'bulk_import'
                  AND identifier_type = 'server_code' AND valid_to IS NULL
                ORDER BY created_at DESC LIMIT 1
            ) code ON true
            """
        ).fetchall()
        for row in server_rows:
            if row["code"]:
                payload = {"server_code": row["code"], **{k: row[k] for k in SHEET_SPECS["服务器"]["headers"] if k != "server_code"}}
                state["server"][row["code"]] = {"id": row["id"], "row_version": row["row_version"], "payload": payload}

        for object_type, id_column, table, identifier_types, sheet_name in (
            ("runtime", "runtime_id", "asset.runtime_instance", ("runtime_code", "legacy_runtime_id", "gateway_id"), "运行时"),
            ("agent", "agent_id", "asset.agent_asset", ("agent_code", "agent_seed_key"), "智能体"),
        ):
            identifiers = connection.execute(
                """
                SELECT identifier_value AS code, runtime_id, agent_id
                FROM asset.external_identifier
                WHERE valid_to IS NULL AND identifier_type = ANY(%(types)s)
                ORDER BY CASE WHEN source_system = 'bulk_import' THEN 0 ELSE 1 END, created_at DESC
                """,
                {"types": list(identifier_types)},
            ).fetchall()
            code_by_id = {}
            for identifier in identifiers:
                target_id = identifier[id_column]
                if target_id is not None and target_id not in code_by_id:
                    code_by_id[target_id] = identifier["code"]
            if object_type == "runtime":
                rows = connection.execute(
                    """
                    SELECT r.runtime_id AS id, r.row_version, s.server_id, f.family_code AS runtime_family_code,
                           r.name, r.runtime_role::text AS runtime_role, r.deployment_mode::text AS deployment_mode,
                           r.desired_version, r.lifecycle_status::text AS lifecycle_status, r.bind_address,
                           r.endpoint_port, r.runtime_account, r.config_path, r.state_path,
                           r.container_name, r.image_ref
                    FROM asset.runtime_instance r
                    JOIN asset.server_asset s ON s.server_id = r.server_id
                    JOIN asset.runtime_family f ON f.runtime_family_id = r.runtime_family_id
                    """
                ).fetchall()
                server_code_by_id = {value["id"]: code for code, value in state["server"].items()}
                for row in rows:
                    code = code_by_id.get(row["id"])
                    if not code:
                        continue
                    payload = {"runtime_code": code, "server_code": server_code_by_id.get(row["server_id"]), **{k: row[k] for k in SHEET_SPECS[sheet_name]["headers"] if k not in {"runtime_code", "server_code"}}}
                    state[object_type][code] = {"id": row["id"], "row_version": row["row_version"], "payload": payload}
            else:
                rows = connection.execute(
                    """
                    SELECT a.agent_id AS id, a.row_version, a.name, a.structure_role::text AS structure_role,
                           a.parent_agent_id, a.service_archetype::text AS service_archetype,
                           a.lifecycle_status::text AS lifecycle_status, od.department_code AS owning_department_code,
                           ba.employee_no AS business_admin_employee_no, ia.employee_no AS it_admin_employee_no,
                           ps.employee_no AS primary_service_employee_no,
                           pd.department_code AS primary_service_department_code,
                           a.business_domain, a.business_scenario, a.description
                    FROM asset.agent_asset a
                    LEFT JOIN org.department od ON od.department_id = a.owning_department_id
                    LEFT JOIN org.department pd ON pd.department_id = a.primary_service_department_id
                    LEFT JOIN org.person ba ON ba.person_id = a.business_admin_person_id
                    LEFT JOIN org.person ia ON ia.person_id = a.it_admin_person_id
                    LEFT JOIN org.person ps ON ps.person_id = a.primary_service_person_id
                    """
                ).fetchall()
                agent_code_by_id = {target_id: code for target_id, code in code_by_id.items()}
                for row in rows:
                    code = code_by_id.get(row["id"])
                    if not code:
                        continue
                    payload = {"agent_code": code, "parent_agent_code": agent_code_by_id.get(row["parent_agent_id"]), **{k: row[k] for k in SHEET_SPECS[sheet_name]["headers"] if k not in {"agent_code", "parent_agent_code"}}}
                    state[object_type][code] = {"id": row["id"], "row_version": row["row_version"], "payload": payload}
        return state

    @staticmethod
    def _add_issue(row, field, code, message):
        row["issues"].append((field, code, message))

    @classmethod
    def _validate_relations(cls, rows, state):
        available = {kind: set(state[kind]) for kind in ("department", "person", "server", "runtime", "agent")}
        imported = defaultdict(dict)
        for row in rows:
            if row["key"]:
                available[row["object_type"]].add(row["key"])
                imported[row["object_type"]][row["key"]] = row

        for row in rows:
            data = row["normalized_payload"]
            kind = row["object_type"]
            if kind == "department":
                parent = data.get("parent_department_code")
                if parent and parent not in available["department"]:
                    cls._add_issue(row, "parent_department_code", "UNKNOWN_REFERENCE", f"部门编码 {parent} 不存在")
                if parent and parent == data.get("department_code"):
                    cls._add_issue(row, "parent_department_code", "HIERARCHY_CYCLE", "父部门不能是自身")
            elif kind == "person":
                department = data.get("primary_department_code")
                if department and department not in available["department"]:
                    cls._add_issue(row, "primary_department_code", "UNKNOWN_REFERENCE", f"部门编码 {department} 不存在")
            elif kind == "server":
                if data.get("environment_code") not in state["environment"]:
                    cls._add_issue(row, "environment_code", "UNKNOWN_REFERENCE", f"环境编码 {data.get('environment_code')} 不存在")
                provider = data.get("provider_code")
                if provider and provider not in state["provider"]:
                    cls._add_issue(row, "provider_code", "UNKNOWN_REFERENCE", f"云厂商编码 {provider} 不存在")
                for field in ("internal_ip", "public_ip"):
                    if data.get(field):
                        try:
                            ipaddress.ip_address(str(data[field]))
                        except ValueError:
                            cls._add_issue(row, field, "INVALID_IP", "不是有效的 IPv4 或 IPv6 地址")
            elif kind == "runtime":
                if data.get("server_code") not in available["server"]:
                    cls._add_issue(row, "server_code", "UNKNOWN_REFERENCE", f"服务器编码 {data.get('server_code')} 不存在")
                if data.get("runtime_family_code") not in state["runtime_family"]:
                    cls._add_issue(row, "runtime_family_code", "UNKNOWN_REFERENCE", f"运行时类型 {data.get('runtime_family_code')} 不存在")
                if data.get("deployment_mode") == "baremetal" and (data.get("container_name") or data.get("image_ref")):
                    cls._add_issue(row, "deployment_mode", "INVALID_DEPLOYMENT_FIELDS", "baremetal 不能填写 container_name 或 image_ref")
                if data.get("deployment_mode") == "container" and (not data.get("container_name") or not data.get("image_ref")):
                    cls._add_issue(row, "deployment_mode", "INVALID_DEPLOYMENT_FIELDS", "container 必须填写 container_name 和 image_ref")
            elif kind == "agent":
                for field, target_kind in (
                    ("owning_department_code", "department"),
                    ("primary_service_department_code", "department"),
                    ("business_admin_employee_no", "person"),
                    ("it_admin_employee_no", "person"),
                    ("primary_service_employee_no", "person"),
                    ("parent_agent_code", "agent"),
                ):
                    value = data.get(field)
                    if value and value not in available[target_kind]:
                        cls._add_issue(row, field, "UNKNOWN_REFERENCE", f"引用编码 {value} 不存在")
                role = data.get("structure_role")
                archetype = data.get("service_archetype")
                parent = data.get("parent_agent_code")
                if role == "subagent":
                    if not parent:
                        cls._add_issue(row, "parent_agent_code", "REQUIRED_FIELD", "子智能体必须填写父智能体编码")
                    parent_row = imported["agent"].get(parent)
                    parent_payload = parent_row and parent_row["normalized_payload"] or state["agent"].get(parent, {}).get("payload")
                    if parent_payload and parent_payload.get("structure_role") != "primary":
                        cls._add_issue(row, "parent_agent_code", "INVALID_PARENT", "父智能体必须是主智能体")
                    forbidden = [
                        "business_admin_employee_no", "it_admin_employee_no", "primary_service_employee_no",
                        "primary_service_department_code", "business_domain", "business_scenario",
                    ]
                    if any(data.get(field) for field in forbidden):
                        cls._add_issue(row, None, "INVALID_SUBAGENT_FIELDS", "子智能体不能填写管理员或服务对象字段")
                elif role == "primary":
                    if parent:
                        cls._add_issue(row, "parent_agent_code", "INVALID_PARENT", "主智能体不能填写父智能体")
                    if archetype == "personal" and not data.get("primary_service_employee_no"):
                        cls._add_issue(row, "primary_service_employee_no", "REQUIRED_FIELD", "个人服务型必须填写服务人员工号")
                    if archetype == "personal" and any(data.get(field) for field in ("primary_service_department_code", "business_domain", "business_scenario")):
                        cls._add_issue(row, "service_archetype", "INVALID_SERVICE_FIELDS", "个人服务型不能填写服务部门、业务域或业务场景")
                    if archetype == "department" and not data.get("primary_service_department_code"):
                        cls._add_issue(row, "primary_service_department_code", "REQUIRED_FIELD", "部门服务型必须填写服务部门编码")
                    if archetype == "department" and any(data.get(field) for field in ("primary_service_employee_no", "business_domain", "business_scenario")):
                        cls._add_issue(row, "service_archetype", "INVALID_SERVICE_FIELDS", "部门服务型不能填写服务人员、业务域或业务场景")
                    if archetype == "business" and (not data.get("business_domain") or not data.get("business_scenario")):
                        cls._add_issue(row, "business_domain", "REQUIRED_FIELD", "业务服务型必须填写业务域和业务场景")
                    if archetype == "business" and (data.get("primary_service_employee_no") or data.get("primary_service_department_code")):
                        cls._add_issue(row, "service_archetype", "INVALID_SERVICE_FIELDS", "业务服务型不能填写服务人员或服务部门")
                    if data.get("lifecycle_status") in {"testing", "active"} and (not data.get("business_admin_employee_no") or not data.get("it_admin_employee_no")):
                        cls._add_issue(row, None, "REQUIRED_ADMIN", "测试中或启用的主智能体必须填写业务管理员和 IT 管理员")

        # Imported hierarchies must be acyclic. Existing parents are leaves for this check.
        for kind, key_field, parent_field in (
            ("department", "department_code", "parent_department_code"),
            ("agent", "agent_code", "parent_agent_code"),
        ):
            candidates = list(imported[kind].values())
            try:
                _topological(candidates, key_field, parent_field)
            except ValueError as exc:
                for candidate in candidates:
                    cls._add_issue(candidate, parent_field, "HIERARCHY_CYCLE", str(exc))

    @staticmethod
    def _summary(rows):
        result = {sheet: {"total": 0, "new": 0, "changed": 0, "unchanged": 0, "invalid": 0} for sheet in SHEET_SPECS}
        for row in rows:
            bucket = result[row["sheet_name"]]
            bucket["total"] += 1
            bucket[row["status"] if row["status"] in bucket else "invalid"] += 1
        errors = sum(len(row["issues"]) for row in rows)
        return {"entities": result, "rows": len(rows), "errors": errors, "warnings": 0}

    def preview(self, file_name, content, actor):
        rows = self._parse(file_name, content)
        digest = hashlib.sha256(content).hexdigest()
        batch_id = uuid4()
        with self.database.connection() as connection:
            state = self._load_state(connection)
            self._validate_relations(rows, state)
            for row in rows:
                existing = state[row["object_type"]].get(row["key"])
                row["target_object_id"] = existing["id"] if existing else uuid4()
                row["expected_row_version"] = existing["row_version"] if existing else None
                before = existing["payload"] if existing else None
                row["before_payload"] = before
                if row["issues"]:
                    row["status"] = "invalid"
                elif not existing:
                    row["status"] = "new"
                elif before == row["normalized_payload"]:
                    row["status"] = "unchanged"
                else:
                    row["status"] = "changed"
            summary = self._summary(rows)
            status = "rejected" if summary["errors"] else "ready"
            connection.execute(
                """
                INSERT INTO ingest.import_batch (
                    import_batch_id, file_name, file_sha256, template_version,
                    import_mode, status, actor_ref, summary
                ) VALUES (%(batch_id)s, %(file_name)s, %(digest)s, %(version)s,
                          'standard', %(status)s, %(actor)s, %(summary)s)
                """,
                {"batch_id": batch_id, "file_name": file_name, "digest": digest, "version": TEMPLATE_VERSION, "status": status, "actor": actor, "summary": Jsonb(summary)},
            )
            for row in rows:
                import_row_id = uuid4()
                connection.execute(
                    """
                    INSERT INTO ingest.import_row (
                        import_row_id, import_batch_id, sheet_name, row_number,
                        target_object_type, target_object_id, expected_row_version,
                        raw_payload, normalized_payload, status
                    ) VALUES (%(row_id)s, %(batch_id)s, %(sheet)s, %(row_number)s,
                              %(object_type)s, %(target_id)s, %(row_version)s,
                              %(raw)s, %(normalized)s, %(status)s)
                    """,
                    {
                        "row_id": import_row_id, "batch_id": batch_id, "sheet": row["sheet_name"],
                        "row_number": row["row_number"], "object_type": row["object_type"],
                        "target_id": row["target_object_id"], "row_version": row["expected_row_version"],
                        "raw": Jsonb(row["raw_payload"]), "normalized": Jsonb(row["normalized_payload"]),
                        "status": row["status"],
                    },
                )
                for field, code, message in row["issues"]:
                    connection.execute(
                        """
                        INSERT INTO ingest.import_issue (
                            import_batch_id, import_row_id, severity, issue_code, field_path, message
                        ) VALUES (%s, %s, 'error', %s, %s, %s)
                        """,
                        (batch_id, import_row_id, code, field, message),
                    )
                if row["status"] in {"new", "changed"}:
                    before = row["before_payload"] or {}
                    for field, after_value in row["normalized_payload"].items():
                        before_value = before.get(field)
                        if before_value != after_value:
                            connection.execute(
                                """
                                INSERT INTO ingest.import_change (
                                    import_row_id, field_path, action, before_value, after_value, selected
                                ) VALUES (%s, %s, %s, %s, %s, true)
                                """,
                                (
                                    import_row_id, field, "clear" if after_value is None else "set",
                                    Jsonb(before_value), Jsonb(after_value),
                                ),
                            )
        return batch_id

    @staticmethod
    def _json(connection, table, id_column, object_id):
        row = connection.execute(
            f"SELECT to_jsonb(record) AS value FROM {table} record WHERE {id_column} = %(object_id)s",
            {"object_id": object_id},
        ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _active_operation(connection, resource_type, object_id):
        if resource_type == "server":
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%(lock_key)s, 0))",
                {"lock_key": f"automation-server:{object_id}"},
            )
            direct = "host.server_id = %(object_id)s"
            plan_key = "locked_server_ids"
        elif resource_type == "runtime":
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
                {"lock_key": f"agent-runtime-deployment:{object_id}"},
            )
            direct = "host.runtime_id = %(object_id)s"
            plan_key = "locked_runtime_ids"
        else:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%(lock_key)s))",
                {"lock_key": f"agent-operation:{object_id}"},
            )
            direct = "host.agent_id = %(object_id)s"
            plan_key = None
        plan_clause = ""
        if plan_key:
            plan_clause = f"""
                OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(
                        COALESCE(host.target_plan -> '{plan_key}', '[]'::jsonb)
                    ) locked(value)
                    WHERE locked.value = %(object_id_text)s
                )
            """
        return connection.execute(
            f"""
            SELECT run.run_id
            FROM automation.operation_host_result host
            JOIN automation.operation_run run ON run.run_id = host.run_id
            WHERE run.status IN ('queued', 'running')
              AND ({direct} {plan_clause})
            ORDER BY run.created_at DESC LIMIT 1
            """,
            {"object_id": object_id, "object_id_text": str(object_id)},
        ).fetchone()

    @staticmethod
    def _audit(connection, batch_id, actor, object_type, object_id, operation, fields, before, after):
        connection.execute(
            """
            INSERT INTO audit.change_log (
                actor_type, actor_ref, source, object_type, object_id, operation,
                changed_fields, before_value, after_value, import_batch_id, reason
            ) VALUES ('import', %(actor)s, 'excel', %(object_type)s, %(object_id)s,
                      %(operation)s, %(fields)s, %(before)s, %(after)s, %(batch_id)s,
                      'Excel 批量导入')
            """,
            {
                "actor": actor, "object_type": object_type, "object_id": object_id,
                "operation": operation, "fields": fields,
                "before": Jsonb(before) if before is not None else None,
                "after": Jsonb(after) if after is not None else None,
                "batch_id": batch_id,
            },
        )

    @staticmethod
    def _identifier(connection, identifier_type, value, target_column, target_id):
        connection.execute(
            f"""
            INSERT INTO asset.external_identifier (
                source_system, identifier_type, identifier_value, {target_column}
            ) VALUES (%(source)s, %(type)s, %(value)s, %(target_id)s)
            ON CONFLICT (source_system, identifier_type, identifier_value)
                WHERE valid_to IS NULL DO NOTHING
            """,
            {"source": SOURCE_SYSTEM, "type": identifier_type, "value": value, "target_id": target_id},
        )
        owner = connection.execute(
            f"""
            SELECT {target_column} AS target_id FROM asset.external_identifier
            WHERE source_system = %(source)s AND identifier_type = %(type)s
              AND identifier_value = %(value)s AND valid_to IS NULL
            """,
            {"source": SOURCE_SYSTEM, "type": identifier_type, "value": value},
        ).fetchone()
        if not owner or owner["target_id"] != target_id:
            raise ConflictError(f"编码 {value} 已关联到其他对象")

    def _commit_department(self, connection, row, ids, batch_id, actor):
        data, object_id = row["normalized_payload"], row["target_object_id"]
        parent_id = ids["department"].get(data.get("parent_department_code"))
        before = self._json(connection, "org.department", "department_id", object_id)
        if row["expected_row_version"] is None:
            connection.execute(
                "INSERT INTO org.department (department_id, department_code, name, parent_department_id, is_active) VALUES (%s, %s, %s, %s, %s)",
                (object_id, data["department_code"], data["name"], parent_id, data["is_active"]),
            )
        else:
            cursor = connection.execute(
                "UPDATE org.department SET name=%s, parent_department_id=%s, is_active=%s WHERE department_id=%s AND row_version=%s",
                (data["name"], parent_id, data["is_active"], object_id, row["expected_row_version"]),
            )
            if cursor.rowcount != 1:
                raise ConflictError(f"部门 {data['department_code']} 已被其他人修改")
        after = self._json(connection, "org.department", "department_id", object_id)
        self._audit(connection, batch_id, actor, "department", object_id, "create" if before is None else "update", row["changed_fields"] or list(data), before, after)

    def _commit_person(self, connection, row, ids, batch_id, actor):
        data, object_id = row["normalized_payload"], row["target_object_id"]
        department_id = ids["department"].get(data.get("primary_department_code"))
        before = self._json(connection, "org.person", "person_id", object_id)
        if row["expected_row_version"] is None:
            connection.execute(
                "INSERT INTO org.person (person_id, employee_no, display_name, primary_department_id, employment_status) VALUES (%s, %s, %s, %s, %s)",
                (object_id, data["employee_no"], data["display_name"], department_id, data["employment_status"]),
            )
        else:
            cursor = connection.execute(
                "UPDATE org.person SET display_name=%s, primary_department_id=%s, employment_status=%s WHERE person_id=%s AND row_version=%s",
                (data["display_name"], department_id, data["employment_status"], object_id, row["expected_row_version"]),
            )
            if cursor.rowcount != 1:
                raise ConflictError(f"人员 {data['employee_no']} 已被其他人修改")
        after = self._json(connection, "org.person", "person_id", object_id)
        self._audit(connection, batch_id, actor, "person", object_id, "create" if before is None else "update", row["changed_fields"] or list(data), before, after)

    def _commit_server(self, connection, row, ids, state, batch_id, actor):
        data, object_id = row["normalized_payload"], row["target_object_id"]
        values = (
            state["environment"][data["environment_code"]], state["provider"].get(data.get("provider_code")),
            data["server_code"], data["name"], data["lifecycle_status"], data.get("cpu_cores"),
            data.get("memory_gib"), data.get("disk_gib"), data.get("internal_ip"), data.get("public_ip"),
            data.get("os_name"), data.get("os_version"), data.get("architecture"), data.get("work_account"),
        )
        before = self._json(connection, "asset.server_asset", "server_id", object_id)
        if row["expected_row_version"] is None:
            connection.execute(
                """
                INSERT INTO asset.server_asset (
                    server_id, environment_id, cloud_provider_id, provider_instance_id, name,
                    lifecycle_status, cpu_cores, memory_gib, disk_gib, internal_ip, public_ip,
                    os_name, os_version, architecture, work_account
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (object_id, *values),
            )
        else:
            active_operation = self._active_operation(connection, "server", object_id)
            if active_operation:
                raise ConflictError(
                    f"服务器 {data['server_code']} 存在执行中的任务 {active_operation['run_id']}"
                )
            if data["lifecycle_status"] in {"disabled", "retired"} and connection.execute(
                """
                SELECT 1 FROM asset.runtime_instance
                WHERE server_id = %(server_id)s AND lifecycle_status = 'active'
                """,
                {"server_id": object_id},
            ).fetchone():
                raise ConflictError(
                    f"服务器 {data['server_code']} 仍有活动运行时，不能停用或退役"
                )
            cursor = connection.execute(
                """
                UPDATE asset.server_asset SET
                    environment_id=%s, cloud_provider_id=%s, provider_instance_id=%s, name=%s,
                    lifecycle_status=%s, cpu_cores=%s, memory_gib=%s, disk_gib=%s,
                    internal_ip=%s, public_ip=%s, os_name=%s, os_version=%s,
                    architecture=%s, work_account=%s
                WHERE server_id=%s AND row_version=%s
                """,
                (*values, object_id, row["expected_row_version"]),
            )
            if cursor.rowcount != 1:
                raise ConflictError(f"服务器 {data['server_code']} 已被其他人修改")
        self._identifier(connection, "server_code", data["server_code"], "server_id", object_id)
        after = self._json(connection, "asset.server_asset", "server_id", object_id)
        self._audit(connection, batch_id, actor, "server_asset", object_id, "create" if before is None else "update", row["changed_fields"] or list(data), before, after)

    def _commit_runtime(self, connection, row, ids, state, batch_id, actor):
        data, object_id = row["normalized_payload"], row["target_object_id"]
        values = (
            ids["server"][data["server_code"]], state["runtime_family"][data["runtime_family_code"]],
            data["name"], data["runtime_role"], data["deployment_mode"], data["lifecycle_status"],
            data.get("bind_address"), data.get("endpoint_port"), data.get("runtime_account"),
            data.get("config_path"), data.get("state_path"), data.get("container_name"),
            data.get("image_ref"), data.get("desired_version"),
        )
        before = self._json(connection, "asset.runtime_instance", "runtime_id", object_id)
        if row["expected_row_version"] is None:
            connection.execute(
                """
                INSERT INTO asset.runtime_instance (
                    runtime_id, server_id, runtime_family_id, name, runtime_role, deployment_mode,
                    lifecycle_status, bind_address, endpoint_port, runtime_account, config_path,
                    state_path, container_name, image_ref, desired_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (object_id, *values),
            )
        else:
            active_operation = self._active_operation(connection, "runtime", object_id)
            if active_operation:
                raise ConflictError(
                    f"运行时 {data['runtime_code']} 存在执行中的任务 {active_operation['run_id']}"
                )
            active_deployment = active_runtime_deployment_state(connection, object_id)
            desired_relationship = {
                "server_id": ids["server"][data["server_code"]],
                "runtime_family_id": state["runtime_family"][data["runtime_family_code"]],
                "deployment_mode": data["deployment_mode"],
                "lifecycle_status": data["lifecycle_status"],
                "config_path": data.get("config_path"),
                "state_path": data.get("state_path"),
            }
            if not unmanaged_runtime_metadata_update_allowed(
                active_deployment, before, desired_relationship,
                allowed_fields={"config_path", "state_path"},
            ):
                raise ConflictError(
                    f"运行时 {data['runtime_code']} 仍有关联智能体，必须先迁移或解除部署"
                )
            cursor = connection.execute(
                """
                UPDATE asset.runtime_instance SET
                    server_id=%s, runtime_family_id=%s, name=%s, runtime_role=%s,
                    deployment_mode=%s, lifecycle_status=%s, bind_address=%s, endpoint_port=%s,
                    runtime_account=%s, config_path=%s, state_path=%s, container_name=%s,
                    image_ref=%s, desired_version=%s
                WHERE runtime_id=%s AND row_version=%s
                """,
                (*values, object_id, row["expected_row_version"]),
            )
            if cursor.rowcount != 1:
                raise ConflictError(f"运行时 {data['runtime_code']} 已被其他人修改")
        self._identifier(connection, "runtime_code", data["runtime_code"], "runtime_id", object_id)
        after = self._json(connection, "asset.runtime_instance", "runtime_id", object_id)
        self._audit(connection, batch_id, actor, "runtime_instance", object_id, "create" if before is None else "update", row["changed_fields"] or list(data), before, after)

    def _commit_agent(self, connection, row, ids, batch_id, actor):
        data, object_id = row["normalized_payload"], row["target_object_id"]
        values = (
            data["name"], data["structure_role"], ids["agent"].get(data.get("parent_agent_code")),
            data["service_archetype"], data["lifecycle_status"], ids["department"].get(data.get("owning_department_code")),
            ids["person"].get(data.get("business_admin_employee_no")), ids["person"].get(data.get("it_admin_employee_no")),
            ids["person"].get(data.get("primary_service_employee_no")), ids["department"].get(data.get("primary_service_department_code")),
            data.get("business_domain"), data.get("business_scenario"), data.get("description"),
        )
        before = self._json(connection, "asset.agent_asset", "agent_id", object_id)
        if row["expected_row_version"] is None:
            parent_agent_id = ids["agent"].get(data.get("parent_agent_code"))
            if parent_agent_id is not None:
                active_operation = self._active_operation(
                    connection, "agent", parent_agent_id,
                )
                if active_operation:
                    raise ConflictError(
                        f"智能体 {data['agent_code']} 的父智能体存在执行中的部署任务 "
                        f"{active_operation['run_id']}"
                    )
            connection.execute(
                """
                INSERT INTO asset.agent_asset (
                    agent_id, name, structure_role, parent_agent_id, service_archetype,
                    lifecycle_status, owning_department_id, business_admin_person_id,
                    it_admin_person_id, primary_service_person_id, primary_service_department_id,
                    business_domain, business_scenario, description
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (object_id, *values),
            )
        else:
            old_operation_agent_id = (
                before.get("parent_agent_id")
                if before.get("structure_role") == "subagent" else object_id
            )
            new_parent_agent_id = ids["agent"].get(data.get("parent_agent_code"))
            new_operation_agent_id = (
                new_parent_agent_id
                if data["structure_role"] == "subagent" else object_id
            )
            for operation_agent_id in sorted(
                {old_operation_agent_id, new_operation_agent_id} - {None}, key=str,
            ):
                active_operation = self._active_operation(
                    connection, "agent", operation_agent_id,
                )
                if active_operation:
                    raise ConflictError(
                        f"智能体 {data['agent_code']} 的成员关系涉及执行中的部署任务 "
                        f"{active_operation['run_id']}"
                    )
            if (
                before["structure_role"] == "primary"
                and (
                    data["structure_role"] != "primary"
                    or data["lifecycle_status"] == "retired"
                )
                and connection.execute(
                    """
                    SELECT 1 FROM asset.agent_deployment
                    WHERE primary_agent_id = %(agent_id)s AND lifecycle_status = 'active'
                    """,
                    {"agent_id": object_id},
                ).fetchone()
            ):
                raise ConflictError(
                    f"智能体 {data['agent_code']} 仍有活动部署，不能改变角色或退役"
                )
            cursor = connection.execute(
                """
                UPDATE asset.agent_asset SET
                    name=%s, structure_role=%s, parent_agent_id=%s, service_archetype=%s,
                    lifecycle_status=%s, owning_department_id=%s, business_admin_person_id=%s,
                    it_admin_person_id=%s, primary_service_person_id=%s,
                    primary_service_department_id=%s, business_domain=%s, business_scenario=%s,
                    description=%s
                WHERE agent_id=%s AND row_version=%s
                """,
                (*values, object_id, row["expected_row_version"]),
            )
            if cursor.rowcount != 1:
                raise ConflictError(f"智能体 {data['agent_code']} 已被其他人修改")
        self._identifier(connection, "agent_code", data["agent_code"], "agent_id", object_id)
        after = self._json(connection, "asset.agent_asset", "agent_id", object_id)
        self._audit(connection, batch_id, actor, "agent_asset", object_id, "create" if before is None else "update", row["changed_fields"] or list(data), before, after)

    def commit(self, batch_id: UUID, actor):
        try:
            with self.database.connection() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtext('agentswatch-bulk-import'))")
                batch = connection.execute(
                    "SELECT status::text AS status FROM ingest.import_batch WHERE import_batch_id=%s FOR UPDATE",
                    (batch_id,),
                ).fetchone()
                if batch is None:
                    raise NotFoundError("import batch not found")
                if batch["status"] == "committed":
                    return batch_id
                if batch["status"] != "ready":
                    raise ValueError("只有校验通过且待确认的批次可以提交")
                if connection.execute("SELECT count(*) AS count FROM ingest.import_issue WHERE import_batch_id=%s AND severity='error'", (batch_id,)).fetchone()["count"]:
                    raise ValueError("批次仍有校验错误，不能提交")
                connection.execute("UPDATE ingest.import_batch SET status='committing', updated_at=now() WHERE import_batch_id=%s", (batch_id,))
                rows = list(connection.execute(
                    """
                    SELECT ir.import_row_id, ir.sheet_name, ir.row_number, ir.target_object_type,
                           ir.target_object_id, ir.expected_row_version, ir.normalized_payload,
                           ir.status::text AS status,
                           COALESCE((
                               SELECT array_agg(change.field_path ORDER BY change.field_path)
                               FROM ingest.import_change change
                               WHERE change.import_row_id = ir.import_row_id AND change.selected
                           ), ARRAY[]::text[]) AS changed_fields
                    FROM ingest.import_row ir WHERE ir.import_batch_id=%s
                    ORDER BY sheet_name, row_number FOR UPDATE
                    """,
                    (batch_id,),
                ).fetchall())
                state = self._load_state(connection)
                ids = {kind: {code: item["id"] for code, item in state[kind].items()} for kind in ("department", "person", "server", "runtime", "agent")}
                for row in rows:
                    if row["status"] in {"new", "changed", "unchanged"}:
                        spec = SHEET_SPECS[row["sheet_name"]]
                        ids[row["target_object_type"]][row["normalized_payload"][spec["key"]]] = row["target_object_id"]
                actionable = [row for row in rows if row["status"] in {"new", "changed"}]
                by_kind = defaultdict(list)
                for row in actionable:
                    by_kind[row["target_object_type"]].append(row)
                for row in _topological(by_kind["department"], "department_code", "parent_department_code"):
                    self._commit_department(connection, row, ids, batch_id, actor)
                for row in by_kind["person"]:
                    self._commit_person(connection, row, ids, batch_id, actor)
                for row in by_kind["server"]:
                    self._commit_server(connection, row, ids, state, batch_id, actor)
                for row in by_kind["runtime"]:
                    self._commit_runtime(connection, row, ids, state, batch_id, actor)
                for row in _topological(by_kind["agent"], "agent_code", "parent_agent_code"):
                    self._commit_agent(connection, row, ids, batch_id, actor)
                connection.execute(
                    "UPDATE ingest.import_row SET status='committed' WHERE import_batch_id=%s AND status IN ('new','changed','unchanged')",
                    (batch_id,),
                )
                connection.execute(
                    "UPDATE ingest.import_batch SET status='committed', committed_at=now(), updated_at=now() WHERE import_batch_id=%s",
                    (batch_id,),
                )
        except (NotFoundError, ValueError):
            raise
        except Exception as exc:
            try:
                with self.database.connection() as connection:
                    connection.execute(
                        """
                        UPDATE ingest.import_batch
                        SET status='failed', updated_at=now(),
                            summary=jsonb_set(summary, '{last_error}', to_jsonb(%(message)s::text), true)
                        WHERE import_batch_id=%(batch_id)s AND status <> 'committed'
                        """,
                        {"batch_id": batch_id, "message": str(exc)[:1000]},
                    )
            except Exception:
                pass
            raise
        return batch_id
