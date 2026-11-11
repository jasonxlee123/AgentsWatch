import { AGENT_ARCHETYPE_LABELS } from "./agentUtils.js";

export const GROUP_RESOURCE_LABELS = { server: "服务器分组", runtime: "运行时分组", agent: "智能体分组" };
export const GROUP_STATUS_LABELS = { active: "使用中", disabled: "已停用", retired: "已退役" };

export function buildDemoGroups(servers, agents, runtimeItems = []) {
  const primaryAgents = agents.filter((agent) => agent.structure_role === "primary");
  const runtimes = runtimeItems.length ? runtimeItems : servers.flatMap((server) => server.runtimes || []);
  const serverGroups = [
    { group_id: "demo-server-prod", group_code: "server-prod", name: "生产服务器组", resource_type: "server", purpose: "生产环境服务器统一分类", lifecycle_status: "active", members: servers.filter((server) => server.lifecycle_status !== "retired") },
    { group_id: "demo-server-capacity", group_code: "server-high-capacity", name: "高规格服务器组", resource_type: "server", purpose: "大内存和高并发运行时承载", lifecycle_status: "active", members: servers.filter((server) => (server.memory_gib || 0) >= 64) },
    { group_id: "demo-server-container", group_code: "server-container", name: "容器宿主机组", resource_type: "server", purpose: "承载容器化运行时", lifecycle_status: "active", members: servers.slice(1, 3) },
  ];
  const runtimeGroups = [
    { group_id: "demo-runtime-openclaw", group_code: "runtime-openclaw", name: "OpenClaw 运行时组", resource_type: "runtime", purpose: "OpenClaw 运行时统一运维", lifecycle_status: "active", members: runtimes.filter((runtime) => runtime.family_code === "openclaw") },
    { group_id: "demo-runtime-active", group_code: "runtime-active", name: "活动运行时组", resource_type: "runtime", purpose: "当前可执行任务的运行时", lifecycle_status: "active", members: runtimes.filter((runtime) => runtime.lifecycle_status === "active") },
  ];
  const agentGroups = [
    { group_id: "demo-agent-priority", group_code: "agent-priority", name: "重点智能体组", resource_type: "agent", purpose: "重点业务保障和运维关注", lifecycle_status: "active", members: primaryAgents },
    { group_id: "demo-agent-department", group_code: "agent-department", name: "部门智能体组", resource_type: "agent", purpose: "部门综合服务智能体", lifecycle_status: "active", members: primaryAgents.filter((agent) => agent.service_archetype === "department") },
    { group_id: "demo-agent-business", group_code: "agent-business", name: "业务场景智能体组", resource_type: "agent", purpose: "垂域业务场景智能体", lifecycle_status: "active", members: primaryAgents.filter((agent) => agent.service_archetype === "business") },
  ];
  return [...serverGroups, ...runtimeGroups, ...agentGroups].map((group, index) => ({ ...group, current_members: group.members.length, row_version: 1, updated_at: `2026-07-12T${String(8 + index).padStart(2, "0")}:00:00+08:00` }));
}

export function filterAssetsByGroup(items, memberIds, idField) {
  if (memberIds === null) return items;
  return items.filter((item) => memberIds.has(item[idField]));
}

export function filterGroups(groups, query, resourceType, lifecycle) {
  const keyword = query.trim().toLocaleLowerCase("zh-CN");
  return groups.filter((group) => {
    if (resourceType !== "all" && group.resource_type !== resourceType) return false;
    if (lifecycle !== "all" && group.lifecycle_status !== lifecycle) return false;
    if (!keyword) return true;
    return [group.name, group.group_code, group.purpose]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(keyword));
  });
}

export function buildDemoGroupDetail(group, servers, agents, allDemoGroups) {
  const known = allDemoGroups.find((item) => item.group_id === group.group_id);
  const source = known || group;
  let members = source.members;
  if (!members) {
    members = source.resource_type === "server"
      ? servers.slice(0, source.current_members || servers.length)
      : source.resource_type === "runtime"
        ? servers.flatMap((server) => server.runtimes || []).slice(0, source.current_members || servers.length)
        : agents.filter((agent) => agent.structure_role === "primary").slice(0, source.current_members || agents.length);
  }
  return {
    ...source,
    row_version: source.row_version || 1,
    current_members: members.length,
    members: members.map((member) => source.resource_type === "server" ? {
      member_id: member.server_id,
      server_id: member.server_id,
      name: member.name,
      lifecycle_status: member.lifecycle_status || "active",
      internal_ip: member.internal_ip,
      public_ip: member.public_ip,
      provider_instance_id: member.provider_instance_id,
      runtime_count: member.observed_agents ? Math.max(1, Math.ceil(member.observed_agents / 4)) : 1,
      valid_from: "2026-06-01T00:00:00+08:00",
    } : source.resource_type === "runtime" ? {
      member_id: member.runtime_id,
      runtime_id: member.runtime_id,
      name: member.name,
      lifecycle_status: member.lifecycle_status || "active",
      deployment_mode: member.deployment_mode || "baremetal",
      family_code: member.family_code || "openclaw",
      server_id: member.server_id,
      server_name: member.server_name,
      server_internal_ip: member.server_internal_ip,
      valid_from: "2026-06-01T00:00:00+08:00",
    } : {
      member_id: member.agent_id,
      agent_id: member.agent_id,
      name: member.name,
      lifecycle_status: member.lifecycle_status || "active",
      service_archetype: member.service_archetype,
      service_archetype_label: AGENT_ARCHETYPE_LABELS[member.service_archetype],
      owning_department_code: member.owning_department_code,
      owning_department_name: member.owning_department_name,
      active_runtime_id: member.active_runtime_id,
      active_runtime_name: member.active_runtime_name,
      valid_from: "2026-06-01T00:00:00+08:00",
    }),
  };
}
