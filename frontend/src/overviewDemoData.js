function agentSet(name, children, serviceArchetype = "business") {
  return {
    primary: { agent_id: `agent-${name}`, name, lifecycle_status: "active", version: "2026.5.28", service_archetype: serviceArchetype },
    subagents: children.map((child, index) => ({ agent_id: `sub-${name}-${index}`, name: child, lifecycle_status: "active", version: index < 2 ? "2026.5.28" : "2026.5.12" })),
  };
}

const baseServers = [
  { server_id: "demo-server-023", name: "示例服务器 023", internal_ip: "192.0.2.23", public_ip: null, deployment_mode: "baremetal", lifecycle_status: "active", environment_code: "demo-production", provider_code: "demo-cloud", provider_instance_id: "demo-instance-023", cpu_cores: 16, memory_gib: 64, disk_gib: 500, architecture: "x86_64", work_account: "demo-operator", os_name: "Linux", os_version: "Rocky 9.4", observed_agents: 7 },
  { server_id: "demo-server-024", name: "示例服务器 024", internal_ip: "192.0.2.24", public_ip: null, deployment_mode: "baremetal", lifecycle_status: "active", environment_code: "demo-production", provider_code: "demo-cloud", provider_instance_id: "demo-instance-024", cpu_cores: 16, memory_gib: 64, disk_gib: 500, architecture: "x86_64", work_account: "demo-operator", os_name: "Linux", os_version: "Rocky 9.4", observed_agents: 4 },
  { server_id: "demo-server-019", name: "示例服务器 019", internal_ip: "192.0.2.19", public_ip: null, deployment_mode: "baremetal", lifecycle_status: "maintenance", environment_code: "demo-production", provider_code: "demo-cloud", provider_instance_id: "demo-instance-019", cpu_cores: 32, memory_gib: 128, disk_gib: 1000, architecture: "x86_64", work_account: "demo-operator", os_name: "Linux", os_version: "Rocky 9.4", observed_agents: 3 },
  { server_id: "demo-server-017", name: "示例服务器 017", internal_ip: "192.0.2.17", public_ip: "198.51.100.17", deployment_mode: "baremetal", lifecycle_status: "active", environment_code: "demo-production", provider_code: "demo-private-cloud", provider_instance_id: "demo-instance-017", cpu_cores: 16, memory_gib: 64, disk_gib: 800, architecture: "aarch64", work_account: "demo-operator", os_name: "Linux", os_version: "Rocky 9.4", observed_agents: 5 },
  { server_id: "demo-server-015", name: "示例服务器 015", internal_ip: "192.0.2.15", public_ip: null, deployment_mode: "baremetal", lifecycle_status: "disabled", environment_code: "demo-production", provider_code: "demo-cloud", provider_instance_id: "demo-instance-015", cpu_cores: 8, memory_gib: 32, disk_gib: 300, architecture: "x86_64", work_account: "demo-operator", os_name: "Linux", os_version: "Rocky 9.4", observed_agents: 6 },
];

const extraServers = Array.from({ length: 28 }, (_, index) => {
  const sequence = index + 101;
  return {
    server_id: `demo-server-${sequence}`,
    name: `示例服务器 ${String(sequence).padStart(3, "0")}`,
    internal_ip: `198.51.100.${index + 20}`,
    public_ip: null,
    deployment_mode: index % 4 === 0 ? "container" : "baremetal",
    lifecycle_status: index % 9 === 0 ? "maintenance" : "active",
    environment_code: index % 5 === 0 ? "demo-staging" : "demo-production",
    provider_code: index % 3 === 0 ? "demo-private-cloud" : "demo-cloud",
    provider_instance_id: `demo-instance-${sequence}`,
    cpu_cores: index % 3 === 0 ? 32 : 16,
    memory_gib: index % 3 === 0 ? 128 : 64,
    disk_gib: 500,
    architecture: index % 6 === 0 ? "aarch64" : "x86_64",
    work_account: "demo-operator",
    os_name: "Linux",
    os_version: "Rocky 9.4",
    observed_agents: index < 21 ? 1 : 0,
  };
});

const auxiliaryRuntimes = Array.from({ length: 6 }, (_, index) => ({
  runtime_id: `demo-runtime-023-aux-${index + 1}`,
  name: `OpenClaw Runtime（示例业务组 ${index + 1}）`,
  family_code: "openclaw",
  desired_version: index < 4 ? "2026.5.28" : "2026.5.20",
  lifecycle_status: "active",
  observed_status: index === 5 ? "degraded" : "healthy",
  config_path: `/opt/openclaw/business-${index + 1}`,
  node_id: `demo-runtime-node-023-aux-${index + 1}`,
  active_primary_agents: 1,
}));

const extraRuntimeEntries = extraServers.slice(0, 21).map((server, index) => [server.server_id, [{
  runtime_id: `demo-runtime-${index + 101}`,
  name: index % 4 === 0 ? "Hermes Runtime" : "OpenClaw Runtime",
  family_code: index % 4 === 0 ? "hermes" : "openclaw",
  desired_version: index % 4 === 0 ? "1.4.2" : "2026.5.28",
  lifecycle_status: "active",
  observed_status: index % 8 === 0 ? "degraded" : "healthy",
  config_path: `/opt/runtime/${index + 101}`,
  node_id: `demo-runtime-node-${index + 101}`,
  active_primary_agents: 1,
}]]);

const runtimes = {
  "demo-server-023": [
    { runtime_id: "demo-runtime-023-main", name: "OpenClaw Runtime", family_code: "openclaw", desired_version: "2026.5.28", lifecycle_status: "active", observed_status: "healthy", config_path: "/opt/openclaw/runtime", node_id: "demo-runtime-node-023-main", active_primary_agents: 1 },
    { runtime_id: "demo-runtime-023-backup", name: "OpenClaw Runtime（备用）", family_code: "openclaw", desired_version: "2026.4.10", lifecycle_status: "disabled", observed_status: "unknown", config_path: "/opt/openclaw/standby", node_id: "demo-runtime-node-023-backup", active_primary_agents: 1 },
    ...auxiliaryRuntimes,
  ],
  "demo-server-024": [{ runtime_id: "demo-runtime-024-main", name: "OpenClaw Runtime", family_code: "openclaw", deployment_mode: "container", desired_version: "2026.5.28", lifecycle_status: "active", observed_status: "healthy", config_path: "/etc/openclaw/runtime", state_path: "/var/lib/openclaw", image_ref: "openclaw/runtime:2026.5.28", endpoint_port: 18789, node_id: "demo-runtime-node-024-main", active_primary_agents: 1 }],
  "demo-server-019": [{ runtime_id: "demo-runtime-019-main", name: "Hermes Runtime", family_code: "hermes", desired_version: "1.4.2", lifecycle_status: "active", observed_status: "healthy", config_path: "/opt/hermes/runtime", node_id: "demo-runtime-node-019-main", active_primary_agents: 1 }],
  "demo-server-017": [{ runtime_id: "demo-runtime-017-main", name: "OpenClaw Runtime", family_code: "openclaw", desired_version: "2026.5.20", lifecycle_status: "active", observed_status: "degraded", config_path: "/opt/openclaw/runtime", node_id: "demo-runtime-node-017-main", active_primary_agents: 1 }],
  "demo-server-015": [{ runtime_id: "demo-runtime-015-main", name: "OpenClaw Runtime", family_code: "openclaw", desired_version: "2026.5.12", lifecycle_status: "active", observed_status: "healthy", config_path: "/opt/openclaw/runtime", node_id: "demo-runtime-node-015-main", active_primary_agents: 1 }],
  ...Object.fromEntries(extraRuntimeEntries),
};

const agents = {
  "demo-runtime-023-main": {
    primary: { agent_id: "demo-agent-compliance", name: "示例合规智能体", lifecycle_status: "active", version: "2026.5.28", service_archetype: "department" },
    subagents: [
      ["示例合同审查", "2026.5.28"], ["示例规则检索", "2026.5.28"], ["示例案例分析", "2026.5.12"],
      ["示例文书生成", "2026.5.12"], ["示例条款比对", "2026.4.30"], ["示例风险提示", "2026.4.30"],
    ].map(([name, version], index) => ({ agent_id: `demo-agent-compliance-sub-${index + 1}`, name, lifecycle_status: "active", version })),
  },
  "demo-runtime-023-backup": agentSet("示例合规智能体（备用）", []),
  "demo-runtime-024-main": agentSet("示例客户服务智能体", ["示例知识检索", "示例工单分类", "示例回复生成"], "personal"),
  "demo-runtime-019-main": agentSet("示例资金洞察智能体", ["示例报表解读", "示例异常检测"]),
  "demo-runtime-017-main": agentSet("示例采购智能体", ["示例供应商检索", "示例比价分析", "示例条款核验", "示例风险扫描"]),
  "demo-runtime-015-main": agentSet("示例研发协作智能体", ["示例文档检索", "示例日志分析", "示例变更评审"], "department"),
  ...Object.fromEntries(auxiliaryRuntimes.map((runtime, index) => [runtime.runtime_id, agentSet(`示例业务组智能体 ${index + 1}`, [])])),
  ...Object.fromEntries(extraRuntimeEntries.map(([, runtimeList], index) => [runtimeList[0].runtime_id, agentSet(`示例智能体 ${index + 1}`, [])])),
};

export const OVERVIEW_DEMO = {
  counts: { servers: 33, runtimes: 33, primaryAgents: 33, completedTasks: 2 },
  servers: [...baseServers, ...extraServers],
  runtimes,
  agents,
};
