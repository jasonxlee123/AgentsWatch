import { displayRuntimeName } from "./serverUtils.js";

export const RUNTIME_STATUS_LABELS = {
  planned: "已规划",
  active: "运行中",
  disabled: "已停用",
  retired: "已退役",
};

export const HEALTH_STATUS_LABELS = {
  unknown: "未知",
  healthy: "健康",
  degraded: "性能下降",
  critical: "严重",
  unreachable: "无法访问",
};

export const RUNTIME_MANAGEMENT_DRIVER_LABELS = {
  none: "暂不管理",
  systemd: "systemd 系统服务",
  systemd_user: "systemd 用户服务",
  launchd: "launchd 用户服务",
  mock: "本机测试 Mock",
};

export function flattenRuntimeMap(runtimeMap, servers) {
  const serverIndex = new Map(servers.map((server) => [server.server_id, server]));
  return Object.entries(runtimeMap).flatMap(([serverId, runtimes]) => {
    const server = serverIndex.get(serverId);
    return runtimes.map((runtime) => ({
      ...runtime,
      server_id: runtime.server_id || serverId,
      server_name: runtime.server_name || server?.name || "未知服务器",
      server_internal_ip: runtime.server_internal_ip || server?.internal_ip || null,
      deployment_mode: runtime.deployment_mode || "baremetal",
    }));
  });
}

export function filterRuntimes(runtimes, query, family, deploymentMode) {
  const keyword = query.trim().toLocaleLowerCase("zh-CN");
  return runtimes.filter((runtime) => {
    if (family !== "all" && runtime.family_code !== family) return false;
    if (deploymentMode !== "all" && runtime.deployment_mode !== deploymentMode) return false;
    if (!keyword) return true;
    return [runtime.name, displayRuntimeName(runtime.name), runtime.server_name, runtime.server_internal_ip, runtime.runtime_id]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(keyword));
  });
}

export function buildDemoRuntimeDetail(runtime, agents = { primary: null, subagents: [] }) {
  const observedCount = (agents.primary ? 1 : 0) + (agents.subagents?.length || 0);
  return {
    ...runtime,
    runtime_role: runtime.runtime_role || "gateway",
    deployment_mode: runtime.deployment_mode || "baremetal",
    lifecycle_status: runtime.lifecycle_status || "active",
    observed_status: runtime.observed_status || "unknown",
    bind_address: runtime.bind_address || "127.0.0.1",
    endpoint_port: runtime.endpoint_port || 18789,
    runtime_account: runtime.runtime_account || "openclaw",
    config_path: runtime.config_path || "/opt/runtime/config",
    state_path: runtime.state_path || "/opt/runtime/state",
    command_path: runtime.command_path || null,
    desired_version: runtime.desired_version || "未设置",
    row_version: runtime.row_version || 1,
    last_discovered_at: runtime.last_discovered_at || "2026-07-12T09:16:00+08:00",
    active_deployments: agents.primary ? [{
      agent_deployment_id: `${runtime.runtime_id}-deployment`,
      agent_id: agents.primary.agent_id,
      agent_name: agents.primary.name,
      service_archetype: agents.primary.service_archetype || "business",
      valid_from: "2026-06-01T00:00:00+08:00",
    }] : [],
    latest_snapshot: {
      snapshot_id: `${runtime.runtime_id}-snapshot`,
      discovered_at: runtime.last_discovered_at || "2026-07-12T09:16:00+08:00",
      discovery_status: "succeeded",
      actual_version: runtime.actual_version || runtime.desired_version || "未发现",
      observed_agent_count: observedCount,
      agent_count_confidence: agents.subagents?.length ? "high" : "medium",
    },
    web_endpoint: null,
    recent_web_access_sessions: [],
  };
}
