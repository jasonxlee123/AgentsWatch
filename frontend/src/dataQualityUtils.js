export const ISSUE_DEFINITIONS = {
  BUSINESS_ADMIN_EMPLOYEE_NO_MISSING: { label: "管理员工号", count: 66, severity: "error", guidance: "根据通讯录确认业务或 IT 管理员的稳定工号，禁止仅使用姓名自动匹配。" },
  DEPARTMENT_CODE_UNKNOWN: { label: "部门编码", count: 33, severity: "error", guidance: "补充或纠正部门编码，并确保编码已存在于 Departments Sheet 或平台部门主数据。" },
  RUNTIME_AGENT_KEY_MISSING: { label: "运行时 Agent Key", count: 33, severity: "warning", guidance: "从运行时发现结果确认 runtime_agent_key；没有明细时保留待处理，不生成虚假 Agent 条目。" },
  SERVICE_ARCHETYPE_REQUIRED: { label: "服务类型", count: 33, severity: "error", guidance: "在 personal、department、business 中唯一选择一种，并按类型填写服务人员、部门或业务场景。" },
  PLACEHOLDER_VALUE: { label: "占位值", count: 2, severity: "warning", guidance: "将“待定”“-”等占位值替换为真实编码；如需清空已有值，必须显式使用 __CLEAR__。" },
  SUBAGENT_DETAIL_MISSING: { label: "Subagent 明细", count: 2, severity: "warning", guidance: "仅有挂载数量时保留低置信度观测计数，不得按数量自动创建子智能体。" },
  EMPTY_MEANS_IGNORE: { label: "空值表示忽略", count: 1, severity: "warning", guidance: "空单元格默认不修改当前值；若确实要清空字段，在 Excel 中填写 __CLEAR__。" },
  ROW_VERSION_CONFLICT: { label: "并发版本冲突", count: 1, severity: "error", guidance: "暂存后主数据已变化，当前批次不可提交。请重新导出并生成差异。" },
};

const DEMO_BATCH = { import_batch_id: "demo-batch-001", file_name: "示例智能体资产.xlsx", batch_status: "ready" };

export function buildDemoIssues() {
  const examples = [
    ["BUSINESS_ADMIN_EMPLOYEE_NO_MISSING", "Agents", 2, "business_admin_employee_no", "示例合规智能体", "agent_asset"],
    ["BUSINESS_ADMIN_EMPLOYEE_NO_MISSING", "Agents", 3, "it_admin_employee_no", "示例客户服务智能体", "agent_asset"],
    ["DEPARTMENT_CODE_UNKNOWN", "Agents", 8, "owning_department_code", "示例资金洞察智能体", "agent_asset"],
    ["DEPARTMENT_CODE_UNKNOWN", "People", 12, "department_code", "DEMO-EMP-021", "person"],
    ["RUNTIME_AGENT_KEY_MISSING", "Runtimes", 5, "runtime_agent_key", "demo-runtime-gateway-03", "runtime_instance"],
    ["RUNTIME_AGENT_KEY_MISSING", "Runtimes", 9, "runtime_agent_key", "demo-runtime-gateway-01", "runtime_instance"],
    ["SERVICE_ARCHETYPE_REQUIRED", "Agents", 11, "service_archetype", "示例采购智能体", "agent_asset"],
    ["SERVICE_ARCHETYPE_REQUIRED", "Agents", 15, "service_archetype", "示例研发协作智能体", "agent_asset"],
    ["PLACEHOLDER_VALUE", "Responsibilities", 7, "employee_no", "待定", "agent_responsibility"],
    ["SUBAGENT_DETAIL_MISSING", "Agents", 18, "subagents", "示例研发协作智能体", "agent_asset"],
  ];
  return examples.map(([code, sheet, row, field, objectName, type], index) => {
    const definition = ISSUE_DEFINITIONS[code];
    const rawValue = code === "PLACEHOLDER_VALUE" ? "待定" : code === "SUBAGENT_DETAIL_MISSING" ? 3 : "";
    return {
      stable_id: `demo-issue-${index + 1}`,
      import_issue_id: `demo-issue-${index + 1}`,
      name: code,
      severity: definition.severity,
      issue_code: code,
      field_path: field,
      message: definition.guidance,
      created_at: `2026-01-15T09:${String(10 + index).padStart(2, "0")}:00+08:00`,
      ...DEMO_BATCH,
      sheet_name: sheet,
      row_number: row,
      target_object_type: type,
      target_object_id: null,
      target_object_name: objectName,
      expected_row_version: 1,
      raw_payload: { name: objectName, [field]: rawValue },
      normalized_payload: { name: objectName, [field]: null },
      row_status: definition.severity === "error" ? "invalid" : "staged",
    };
  });
}

export function issueDefinition(code, fallbackSeverity = "warning") {
  return ISSUE_DEFINITIONS[code] || { label: code, count: 1, severity: fallbackSeverity, guidance: "根据错误信息核对原始 Excel 和当前主数据，重新生成差异后再提交。" };
}

export function buildIssueCategories(issues, useContractTotals = false) {
  const codes = [...new Set(issues.map((issue) => issue.issue_code))];
  return codes.map((code) => {
    const definition = issueDefinition(code, issues.find((issue) => issue.issue_code === code)?.severity);
    return {
      code,
      label: definition.label,
      severity: definition.severity,
      count: useContractTotals ? definition.count : issues.filter((issue) => issue.issue_code === code).length,
    };
  }).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "zh-CN"));
}

export function filterIssues(issues, query, severity, issueCode, batchId) {
  const keyword = query.trim().toLocaleLowerCase("zh-CN");
  return issues.filter((issue) => {
    if (severity !== "all" && issue.severity !== severity) return false;
    if (issueCode !== "all" && issue.issue_code !== issueCode) return false;
    if (batchId !== "all" && issue.import_batch_id !== batchId) return false;
    if (!keyword) return true;
    return [issue.issue_code, issue.message, issue.file_name, issue.sheet_name, issue.field_path, issue.target_object_name, issue.target_object_type]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(keyword));
  });
}

export function valueAtField(payload, fieldPath) {
  if (!payload || !fieldPath) return null;
  return fieldPath.split(".").reduce((value, key) => value?.[key], payload);
}
