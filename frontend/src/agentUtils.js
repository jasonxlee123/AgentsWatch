import { displayRuntimeName } from "./serverUtils.js";

export const AGENT_ROLE_LABELS = { primary: "主智能体", subagent: "子智能体" };
export const AGENT_ARCHETYPE_LABELS = { personal: "个人智能体", department: "部门智能体", business: "业务智能体" };
export const AGENT_STATUS_LABELS = { requested: "已申请", testing: "测试中", active: "运行中", suspended: "已暂停", retired: "已退役" };

const PRIMARY_PROFILES = {
  "示例合规智能体": { department: "示例合规部", departmentCode: "DEMO-COMPLIANCE", businessAdmin: ["DEMO-EMP-001", "示例业务管理员甲"], itAdmin: ["DEMO-EMP-002", "示例技术管理员甲"], targetDepartment: "DEMO-COMPLIANCE", description: "为示例合规部提供示例审查、规则检索和风险提示。" },
  "示例客户服务智能体": { department: "示例客户服务部", departmentCode: "DEMO-SERVICE", businessAdmin: ["DEMO-EMP-003", "示例业务管理员乙"], itAdmin: ["DEMO-EMP-002", "示例技术管理员甲"], targetPerson: ["DEMO-EMP-004", "示例服务人员甲"], description: "示例人员的客户服务数字助手。" },
  "示例资金洞察智能体": { department: "示例财务部", departmentCode: "DEMO-FINANCE", businessAdmin: ["DEMO-EMP-005", "示例业务管理员丙"], itAdmin: ["DEMO-EMP-002", "示例技术管理员甲"], domain: "示例财务管理", scenario: "示例报表分析与异常识别", description: "辅助示例财务报表解读和异常指标分析。" },
  "示例采购智能体": { department: "示例采购部", departmentCode: "DEMO-PROCUREMENT", businessAdmin: ["DEMO-EMP-006", "示例业务管理员丁"], itAdmin: ["DEMO-EMP-002", "示例技术管理员甲"], domain: "示例采购管理", scenario: "示例供应商比价与风险核验", description: "对示例供应商、价格和采购条款进行专项分析。" },
  "示例研发协作智能体": { department: "示例研发部", departmentCode: "DEMO-ENGINEERING", businessAdmin: ["DEMO-EMP-007", "示例业务管理员戊"], itAdmin: ["DEMO-EMP-002", "示例技术管理员甲"], targetDepartment: "DEMO-ENGINEERING", description: "为示例研发部提供文档检索、日志分析和变更评审。" },
};

export function flattenAgentMap(agentMap, runtimeMap) {
  const runtimeIndex = new Map(Object.values(runtimeMap).flat().map((runtime) => [runtime.runtime_id, runtime]));
  return Object.entries(agentMap).flatMap(([runtimeId, set]) => {
    if (!set?.primary) return [];
    const runtime = runtimeIndex.get(runtimeId);
    const profile = PRIMARY_PROFILES[set.primary.name] || {};
    const primary = {
      ...set.primary,
      structure_role: "primary",
      lifecycle_status: set.primary.lifecycle_status || "active",
      owning_department_code: profile.departmentCode || "DEMO-OPS",
      owning_department_name: profile.department || "示例平台运维部",
      business_admin_name: profile.businessAdmin?.[1] || "待确认",
      it_admin_name: profile.itAdmin?.[1] || "待确认",
      active_runtime_id: runtimeId,
      active_runtime_name: runtime?.name || "未知运行时",
    };
    const subagents = (set.subagents || []).map((agent) => ({
      ...agent,
      structure_role: "subagent",
      service_archetype: primary.service_archetype,
      lifecycle_status: agent.lifecycle_status || "active",
      owning_department_code: primary.owning_department_code,
      owning_department_name: primary.owning_department_name,
      parent_agent_id: primary.agent_id,
      parent_agent_name: primary.name,
      active_runtime_id: runtimeId,
      active_runtime_name: runtime?.name || "未知运行时",
    }));
    return [primary, ...subagents];
  });
}

export function filterAgents(agents, query, role, archetype, lifecycle = "all") {
  const keyword = query.trim().toLocaleLowerCase("zh-CN");
  return agents.filter((agent) => {
    if (role !== "all" && agent.structure_role !== role) return false;
    if (archetype !== "all" && agent.service_archetype !== archetype) return false;
    if (lifecycle !== "all" && agent.lifecycle_status !== lifecycle) return false;
    if (!keyword) return true;
    return [agent.name, agent.agent_id, agent.owning_department_name, agent.owning_department_code, agent.parent_agent_name, agent.active_runtime_name]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(keyword));
  });
}

export function buildDemoAgentDetail(agent, allAgents) {
  const primary = agent.structure_role === "primary" ? agent : allAgents.find((item) => item.agent_id === agent.parent_agent_id);
  const profile = PRIMARY_PROFILES[primary?.name] || {};
  const subagents = allAgents.filter((item) => item.parent_agent_id === primary?.agent_id).map((item) => ({ agent_id: item.agent_id, name: item.name, lifecycle_status: item.lifecycle_status }));
  const businessAdmin = profile.businessAdmin || ["DEMO-EMP-000", "待确认"];
  const itAdmin = profile.itAdmin || ["DEMO-EMP-000", "待确认"];
  return {
    ...agent,
    business_admin_employee_no: businessAdmin[0],
    business_admin_name: businessAdmin[1],
    it_admin_employee_no: itAdmin[0],
    it_admin_name: itAdmin[1],
    primary_service_employee_no: profile.targetPerson?.[0] || null,
    primary_service_person_name: profile.targetPerson?.[1] || null,
    primary_service_department_code: profile.targetDepartment || null,
    business_domain: profile.domain || null,
    business_scenario: profile.scenario || null,
    description: agent.structure_role === "subagent" ? `跟随主智能体“${primary?.name}”部署的稳定子智能体。` : profile.description || "尚未填写智能体说明。",
    row_version: agent.row_version || 1,
    channel: { channel_type: "none" },
    subagents: agent.structure_role === "primary" ? subagents : [],
    accessible_people: agent.structure_role === "primary" ? [
      { person_id: `${primary.agent_id}-access-1`, employee_no: "DEMO-EMP-101", display_name: "示例可访问人员甲" },
      { person_id: `${primary.agent_id}-access-2`, employee_no: "DEMO-EMP-102", display_name: "示例可访问人员乙" },
      { person_id: `${primary.agent_id}-access-3`, employee_no: "DEMO-EMP-103", display_name: "示例可访问人员丙" },
    ] : [],
    groups: agent.structure_role === "primary" ? [{ group_id: `${primary.agent_id}-group`, group_code: "AGENT-BUSINESS", name: "业务智能体组" }] : [],
    active_deployment: primary?.active_runtime_id ? {
      agent_deployment_id: `${primary.agent_id}-deployment`, runtime_id: primary.active_runtime_id,
      runtime_name: displayRuntimeName(primary.active_runtime_name), family_code: String(primary.active_runtime_name).toLowerCase().includes("hermes") ? "hermes" : "openclaw",
      valid_from: "2026-06-01T00:00:00+08:00",
    } : null,
    network_requirements: agent.structure_role === "primary" ? [
      { network_requirement_id: `${primary.agent_id}-net-1`, system_name: "企业知识库", target_type: "domain", target_value: "knowledge.internal", protocol: "https", port_from: 443, port_to: 443, purpose: "业务知识检索" },
      { network_requirement_id: `${primary.agent_id}-net-2`, system_name: "统一身份服务", target_type: "domain", target_value: "identity.internal", protocol: "https", port_from: 443, port_to: 443, purpose: "用户身份校验" },
    ] : [],
  };
}
