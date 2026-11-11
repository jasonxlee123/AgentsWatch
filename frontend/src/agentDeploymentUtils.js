export const AGENT_DEPLOYMENT_ACTIONS = Object.freeze({
  deploy: "agent.apply_deployment",
  sync: "agent.apply_deployment",
  migrate: "agent.migrate_deployment",
  remove: "agent.remove_deployment",
});

function currentDeploymentMembers(agent) {
  return Array.isArray(agent?.deployment_members) ? agent.deployment_members : [];
}

export function primaryDeploymentMember(agent) {
  return currentDeploymentMembers(agent).find((item) => item.member_role === "primary") || null;
}

export function deploymentRuntimeKeyNeedsResolution(agent) {
  if (!agent?.active_deployment) return false;
  const primary = primaryDeploymentMember(agent);
  return !primary
    || primary.mapping_status !== "verified"
    || !String(primary.runtime_agent_key || "").trim();
}

export function hasUnsupportedImportedChildMembers(agent) {
  if (!agent?.active_deployment) return false;
  return currentDeploymentMembers(agent).some((item) => (
    item.member_role !== "primary"
    && (
      item.mapping_status !== "verified"
      || !String(item.runtime_agent_key || "").trim()
    )
  ));
}

export function isLegacyUnmanagedDeployment(agent) {
  const members = currentDeploymentMembers(agent);
  return Boolean(
    agent?.active_deployment
    && members.length
    && members.every((item) => (
      item.mapping_status === "unresolved"
      && (item.runtime_agent_key === null || item.runtime_agent_key === undefined)
    )),
  );
}

export function deploymentStructureNeedsSync(agent) {
  if (!agent?.active_deployment || !agent?.agent_id) return false;
  const members = currentDeploymentMembers(agent);
  if (members.some((item) => !item.agent_id)) return false;
  const desiredIds = new Set([
    agent.agent_id,
    ...(agent.subagents || [])
      .filter((item) => item.lifecycle_status !== "retired")
      .map((item) => item.agent_id),
  ]);
  const currentIds = new Set(members.map((item) => item.agent_id));
  return desiredIds.size !== currentIds.size
    || [...desiredIds].some((agentId) => !currentIds.has(agentId));
}

export function availableAgentDeploymentActions(agent) {
  const lifecycleAllowsDeployment = ["testing", "active"].includes(agent?.lifecycle_status);
  if (agent?.active_deployment) {
    const members = currentDeploymentMembers(agent);
    if (members.length === 0) return [];
    const primaryMembers = members.filter((item) => item.member_role === "primary");
    if (
      primaryMembers.length !== 1
      || (agent.agent_id && primaryMembers[0].agent_id !== agent.agent_id)
    ) return [];
    if (hasUnsupportedImportedChildMembers(agent)) {
      return isLegacyUnmanagedDeployment(agent) ? ["remove"] : [];
    }
    const needsResolution = deploymentRuntimeKeyNeedsResolution(agent);
    const metadataOnlyRemoval = isLegacyUnmanagedDeployment(agent);
    if (deploymentStructureNeedsSync(agent)) {
      return lifecycleAllowsDeployment ? ["sync"] : [];
    }
    if (!lifecycleAllowsDeployment) {
      return needsResolution ? (metadataOnlyRemoval ? ["remove"] : []) : ["remove"];
    }
    if (needsResolution) return metadataOnlyRemoval ? ["sync", "remove"] : ["sync"];
    return ["sync", "migrate", "remove"];
  }
  return lifecycleAllowsDeployment ? ["deploy"] : [];
}

export function defaultRuntimeAgentKey(agent) {
  const normalized = String(agent?.name || "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "")
    .slice(0, 64);
  if (normalized) return normalized;
  const suffix = String(agent?.agent_id || "agent").replaceAll("-", "").slice(0, 8);
  return `agent-${suffix || "main"}`;
}

function backendRuntimeConflict(runtime) {
  return runtime.deployment_blocked_reason
    || runtime.assignment_blocked_reason
    || runtime.deployment_eligibility?.reason
    || "";
}

function runtimeTargetBlocked(runtime) {
  return runtime.deployment_eligible === false
    || runtime.deployment_eligibility?.eligible === false
    || Boolean(backendRuntimeConflict(runtime));
}

export function runtimeSupportsAgentDeployment(runtime) {
  return Boolean(
    runtime
    && runtime.family_code === "openclaw"
    && runtime.deployment_mode === "baremetal"
    && runtime.lifecycle_status === "active"
    && runtime.management_driver
    && runtime.management_driver !== "none"
    && runtime.management_target,
  );
}

export function currentRuntimeDeploymentBlockReason(runtimes, activeDeployment) {
  if (!activeDeployment) return "";
  const runtime = (runtimes || []).find((item) => item.runtime_id === activeDeployment.runtime_id);
  if (!runtime) return "当前部署对应的运行时已不在活动清单中，请刷新页面或联系管理员核查运行时状态。";
  const conflict = runtime.source_operation_blocked_reason || runtime.management_operation_blocked_reason;
  if (conflict) return conflict;
  const backendReason = backendRuntimeConflict(runtime);
  if (runtimeTargetBlocked(runtime) && backendReason) return backendReason;
  if (runtime.deployment_eligible === false || runtime.deployment_eligibility?.eligible === false) {
    return "当前运行时尚未满足智能体部署操作条件，请先检查宿主连接、公钥纳管及运行时配置。";
  }
  if (!runtime.management_driver || runtime.management_driver === "none" || !runtime.management_target) {
    return "当前运行时尚未完成服务管理配置，不能安全执行同步、更换或解除；请先在运行时模块配置管理驱动与服务。";
  }
  if (!runtimeSupportsAgentDeployment(runtime)) {
    return "当前运行时不满足活动裸金属 OpenClaw 部署条件，不能安全修改部署关系。";
  }
  return "";
}

export function eligibleAgentRuntimes(runtimes, activeDeployment = null, action = null) {
  const selectedAction = action || (activeDeployment ? "sync" : "deploy");
  const currentRuntimeId = activeDeployment?.runtime_id;
  if (["sync", "migrate", "remove"].includes(selectedAction)
    && currentRuntimeDeploymentBlockReason(runtimes, activeDeployment)) return [];
  if (selectedAction === "remove") return [];
  return (runtimes || []).filter((runtime) => {
    if (selectedAction === "sync") {
      return runtime.runtime_id === currentRuntimeId
        && runtimeSupportsAgentDeployment(runtime);
    }
    if (!runtimeSupportsAgentDeployment(runtime) || runtimeTargetBlocked(runtime)) return false;
    if (selectedAction === "migrate" && runtime.runtime_id === currentRuntimeId) return false;
    return Number(runtime.active_primary_agents || 0) === 0;
  });
}

export function runtimeCandidateEmptyReason(runtimes, activeDeployment = null, action = null) {
  const selectedAction = action || (activeDeployment ? "sync" : "deploy");
  if (eligibleAgentRuntimes(runtimes, activeDeployment, selectedAction).length) return "";
  if (selectedAction === "sync") {
    if (!activeDeployment) return "当前智能体尚未建立运行时部署关系。";
    const sourceReason = currentRuntimeDeploymentBlockReason(runtimes, activeDeployment);
    if (sourceReason) return sourceReason;
    return "当前运行时暂不可同步，请刷新后重试或在任务中心检查活动任务。";
  }

  if (selectedAction === "migrate") {
    const sourceReason = currentRuntimeDeploymentBlockReason(runtimes, activeDeployment);
    if (sourceReason) return sourceReason;
  }

  const currentRuntimeId = activeDeployment?.runtime_id;
  const alternatives = (runtimes || []).filter((runtime) => runtime.runtime_id !== currentRuntimeId);
  const compatible = alternatives.filter(runtimeSupportsAgentDeployment);
  const backendReason = alternatives.map(backendRuntimeConflict).find(Boolean);
  if (compatible.length && compatible.every((runtime) => (
    Number(runtime.active_primary_agents || 0) > 0 || runtimeTargetBlocked(runtime)
  ))) {
    if (backendReason && compatible.length === 1) return backendReason;
    return selectedAction === "migrate"
      ? "所有可迁移的运行时均已被其他主智能体占用，请先解除一个部署关系或新增运行时。"
      : "所有符合条件的运行时均已被其他主智能体占用，请先解除一个部署关系或新增运行时。";
  }
  if (backendReason) return backendReason;
  return selectedAction === "migrate"
    ? "除当前运行时外，暂无已启用且完成服务管理配置的裸金属 OpenClaw 运行时。"
    : "暂无已启用且完成服务管理配置的裸金属 OpenClaw 运行时，请先在运行时模块完成配置。";
}

export function agentDeploymentDefaults(agent, deployment, members = []) {
  const primaryMember = members.find((item) => item.member_role === "primary");
  return {
    runtime_id: deployment?.runtime_id || "",
    runtime_agent_key: deployment
      ? String(primaryMember?.runtime_agent_key || "")
      : defaultRuntimeAgentKey(agent),
    workspace_root: "",
    model: "",
    risk_acknowledged: false,
  };
}

export function buildAgentDeploymentPayload(form, action = "deploy", deployment = null) {
  if (action === "remove") {
    return deployment?.agent_deployment_id
      ? { expected_deployment_id: deployment.agent_deployment_id }
      : {};
  }
  const parameters = {
    runtime_id: form.runtime_id,
    runtime_agent_key: form.runtime_agent_key.trim(),
  };
  if (action === "migrate") {
    if (deployment?.agent_deployment_id) parameters.expected_deployment_id = deployment.agent_deployment_id;
  }
  if (form.workspace_root.trim()) parameters.workspace_root = form.workspace_root.trim();
  if (form.model.trim()) parameters.model = form.model.trim();
  return parameters;
}

export function agentDeploymentOperationCode(action) {
  const operationCode = AGENT_DEPLOYMENT_ACTIONS[action];
  if (!operationCode) throw new Error(`unsupported agent deployment action: ${action}`);
  return operationCode;
}

function operationHost(run) {
  return run?.hosts?.[0] || run || {};
}

export function agentDeploymentRecoveryStatus(run) {
  const host = operationHost(run);
  const result = host.result || {};
  const status = run?.status || run?.run_status || host.status || host.host_status;
  if (status === "succeeded" || ["queued", "running"].includes(status)) return "";
  if (result.recovery_required) return "需要人工恢复";
  if (result.source_restored && result.target_cleaned === false) return "原部署已恢复，目标预置待清理";
  if (result.source_restored) return "原部署已自动恢复";
  if (result.target_cleaned === false) return "目标预置待清理";
  return "";
}

export function agentDeploymentResultMessage(action, run) {
  const host = operationHost(run);
  const result = host.result || {};
  const status = run?.status || run?.run_status || host.status || host.host_status;
  const error = host.error_message || "请在任务中心查看明细";
  const deployed = result.deployed_agent_keys?.length || result.migrated_agent_keys?.length || 0;
  if (status === "succeeded") {
    if (action === "remove" && (result.remote_cleanup_skipped || result.remote_cleanup_not_required)) {
      return "导入关系已解除；未连接或修改远端运行时";
    }
    if (action === "remove") return "运行时部署已解除；智能体 identity 与工作区仍保留";
    if (action === "migrate") return `运行时更换完成：已迁移 ${deployed} 个智能体`;
    return `部署同步完成：已配置 ${deployed} 个智能体`;
  }
  if (action === "migrate") {
    if (result.recovery_required) {
      const source = result.source_restored ? "源运行时已恢复" : "源运行时未确认恢复";
      const target = result.target_cleaned ? "目标预置已清理" : "目标预置未确认清理";
      return `迁移未完成，需要人工恢复：${source}，${target}。${error}`;
    }
    if (result.source_restored) {
      return `迁移失败，原部署已自动恢复${result.target_cleaned === false ? "，目标预置待清理" : ""}：${error}`;
    }
    return `更换运行时任务执行失败，部署关系未切换：${error}`;
  }
  if (action === "remove") return `解除部署失败，原部署关系保持有效：${error}`;
  return `${action === "sync" ? "同步部署" : "首次部署"}任务执行失败：${error}`;
}
