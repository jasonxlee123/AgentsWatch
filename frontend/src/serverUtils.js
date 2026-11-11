export const SERVER_STATUS_LABELS = {
  planned: "已规划",
  active: "运行中",
  maintenance: "维护中",
  disabled: "已停用",
  retired: "已退役",
};

export function displayRuntimeName(name) {
  return String(name || "未命名运行时").replace(/Runtimes?\b/g, "运行时");
}

export function formatServerMetric(value) {
  if (value === null || value === undefined || value === "") return "0";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return numeric.toLocaleString("zh-CN", { maximumFractionDigits: 2, useGrouping: false });
}

export function formatServerConfig(server) {
  return `${formatServerMetric(server.cpu_cores)}C / ${formatServerMetric(server.memory_gib)}G / ${formatServerMetric(server.disk_gib)}G`;
}

export function serverConnectionDefaults(server = {}, connection = null) {
  const editing = Boolean(connection);
  return {
    host: editing ? connection.host ?? "" : server.internal_ip ?? "",
    username: editing ? connection.username ?? "" : server.work_account ?? "",
  };
}

export function filterServers(servers, query, status) {
  const keyword = query.trim().toLocaleLowerCase("zh-CN");
  return servers.filter((server) => {
    if (status !== "all" && (server.lifecycle_status || "active") !== status) return false;
    if (!keyword) return true;
    return [server.name, server.internal_ip, server.public_ip, server.provider_instance_id, server.provider_code]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(keyword));
  });
}

export function buildDemoServerDetail(server, runtimes = []) {
  return {
    ...server,
    lifecycle_status: server.lifecycle_status || "active",
    environment_code: server.environment_code || "prod-cn",
    provider_code: server.provider_code || "huawei-cloud",
    provider_instance_id: server.provider_instance_id || server.name,
    cpu_cores: server.cpu_cores || 16,
    memory_gib: server.memory_gib || 64,
    disk_gib: server.disk_gib || 500,
    public_ip: server.public_ip || null,
    architecture: server.architecture || "x86_64",
    work_account: server.work_account || "openclaw",
    row_version: server.row_version || 1,
    updated_at: server.updated_at || "2026-07-12T09:18:24+08:00",
    runtimes,
    groups: server.groups || [
      { group_id: `${server.server_id}-group-region`, group_code: "SERVER-BJ", name: "北京区域服务器" },
      { group_id: `${server.server_id}-group-purpose`, group_code: "SERVER-AGENT", name: "智能体运行服务器" },
    ],
  };
}
