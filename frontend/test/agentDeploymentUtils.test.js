import test from "node:test";
import assert from "node:assert/strict";
import {
  agentDeploymentDefaults,
  agentDeploymentOperationCode,
  agentDeploymentRecoveryStatus,
  agentDeploymentResultMessage,
  availableAgentDeploymentActions,
  buildAgentDeploymentPayload,
  currentRuntimeDeploymentBlockReason,
  defaultRuntimeAgentKey,
  deploymentRuntimeKeyNeedsResolution,
  deploymentStructureNeedsSync,
  eligibleAgentRuntimes,
  hasUnsupportedImportedChildMembers,
  isLegacyUnmanagedDeployment,
  runtimeCandidateEmptyReason,
  runtimeSupportsAgentDeployment,
} from "../src/agentDeploymentUtils.js";

test("builds a stable fallback runtime key for Chinese agent names", () => {
  assert.equal(
    defaultRuntimeAgentKey({ name: "法律助手", agent_id: "12345678-0000-4000-8000-000000000001" }),
    "agent-12345678",
  );
  assert.equal(defaultRuntimeAgentKey({ name: "Finance Copilot" }), "finance-copilot");
});

test("keeps removal available for a suspended agent with an active deployment", () => {
  assert.deepEqual(
    availableAgentDeploymentActions({
      lifecycle_status: "suspended",
      active_deployment: { runtime_id: "r1" },
      deployment_members: [{ member_role: "primary", mapping_status: "verified", runtime_agent_key: "main" }],
    }),
    ["remove"],
  );
  assert.deepEqual(
    availableAgentDeploymentActions({ lifecycle_status: "suspended", active_deployment: { runtime_id: "r1" } }),
    [],
  );
  assert.deepEqual(
    availableAgentDeploymentActions({ lifecycle_status: "active", active_deployment: { runtime_id: "r1" } }),
    [],
  );
  assert.deepEqual(availableAgentDeploymentActions({ lifecycle_status: "suspended" }), []);
  assert.deepEqual(
    availableAgentDeploymentActions({
      lifecycle_status: "active",
      active_deployment: { runtime_id: "r1" },
      deployment_members: [{ member_role: "primary", mapping_status: "verified", runtime_agent_key: "main" }],
    }),
    ["sync", "migrate", "remove"],
  );
  assert.deepEqual(availableAgentDeploymentActions({ lifecycle_status: "testing" }), ["deploy"]);
});

test("requires synchronization before migration or removal when child membership drifted", () => {
  const base = {
    agent_id: "primary-a",
    lifecycle_status: "active",
    active_deployment: { runtime_id: "r1" },
    deployment_members: [
      { agent_id: "primary-a", member_role: "primary", mapping_status: "verified", runtime_agent_key: "main" },
    ],
  };
  const addedChild = {
    ...base,
    subagents: [{ agent_id: "child-a", lifecycle_status: "active" }],
  };
  assert.equal(deploymentStructureNeedsSync(addedChild), true);
  assert.deepEqual(availableAgentDeploymentActions(addedChild), ["sync"]);

  const retiredChild = {
    ...base,
    subagents: [{ agent_id: "child-a", lifecycle_status: "retired" }],
    deployment_members: [
      ...base.deployment_members,
      { agent_id: "child-a", member_role: "subagent", mapping_status: "verified", runtime_agent_key: "child" },
    ],
  };
  assert.equal(deploymentStructureNeedsSync(retiredChild), true);
  assert.deepEqual(availableAgentDeploymentActions(retiredChild), ["sync"]);
  assert.deepEqual(
    availableAgentDeploymentActions({ ...retiredChild, lifecycle_status: "suspended" }),
    [],
  );

  assert.deepEqual(availableAgentDeploymentActions({
    ...base,
    deployment_members: [
      { agent_id: "someone-else", member_role: "primary", mapping_status: "verified", runtime_agent_key: "main" },
    ],
  }), []);
});

test("requires an explicit existing OpenClaw ID before an imported relationship can migrate", () => {
  const unresolved = {
    name: "法律助手",
    agent_id: "12345678-0000-4000-8000-000000000001",
    lifecycle_status: "active",
    active_deployment: { agent_deployment_id: "d1", runtime_id: "r1" },
    deployment_members: [{
      agent_id: "12345678-0000-4000-8000-000000000001",
      member_role: "primary",
      mapping_status: "unresolved",
      runtime_agent_key: null,
    }],
  };
  assert.equal(deploymentRuntimeKeyNeedsResolution(unresolved), true);
  assert.deepEqual(availableAgentDeploymentActions(unresolved), ["sync", "remove"]);
  const planned = {
    ...unresolved,
    deployment_members: [{
      agent_id: unresolved.agent_id,
      member_role: "primary",
      mapping_status: "planned",
      runtime_agent_key: "candidate-main",
    }],
  };
  assert.equal(deploymentRuntimeKeyNeedsResolution(planned), true);
  assert.deepEqual(availableAgentDeploymentActions(planned), ["sync"]);
  assert.deepEqual(
    availableAgentDeploymentActions({ ...planned, lifecycle_status: "suspended" }),
    [],
  );
  assert.equal(
    agentDeploymentDefaults(unresolved, unresolved.active_deployment, unresolved.deployment_members).runtime_agent_key,
    "",
  );
  assert.equal(
    agentDeploymentDefaults(unresolved, null, []).runtime_agent_key,
    "agent-12345678",
  );
});

test("only treats a fully unresolved current deployment as metadata-only removable", () => {
  const base = {
    active_deployment: { agent_deployment_id: "d1", runtime_id: "r1" },
  };
  assert.equal(isLegacyUnmanagedDeployment({
    ...base,
    deployment_members: [
      { member_role: "primary", mapping_status: "unresolved", runtime_agent_key: null },
      { member_role: "subagent", mapping_status: "unresolved", runtime_agent_key: null },
    ],
  }), true);
  assert.equal(isLegacyUnmanagedDeployment({
    ...base,
    deployment_members: [
      { member_role: "primary", mapping_status: "unresolved", runtime_agent_key: null },
      { member_role: "subagent", mapping_status: "verified", runtime_agent_key: "child" },
    ],
  }), false);
  assert.equal(isLegacyUnmanagedDeployment({ ...base, deployment_members: [] }), false);
  assert.equal(isLegacyUnmanagedDeployment({
    ...base,
    deployment_members: [{ member_role: "primary", mapping_status: "unresolved", runtime_agent_key: "" }],
  }), false);
  assert.equal(isLegacyUnmanagedDeployment({
    deployment_members: [{ member_role: "primary", mapping_status: "unresolved", runtime_agent_key: null }],
  }), false);
});

test("fails closed when an imported relationship contains an unresolved child member", () => {
  const fullyUnresolved = {
    lifecycle_status: "active",
    active_deployment: { agent_deployment_id: "d1", runtime_id: "r1" },
    deployment_members: [
      { member_role: "primary", mapping_status: "unresolved", runtime_agent_key: null },
      { member_role: "subagent", mapping_status: "unresolved", runtime_agent_key: null },
    ],
  };
  assert.equal(hasUnsupportedImportedChildMembers(fullyUnresolved), true);
  assert.deepEqual(availableAgentDeploymentActions(fullyUnresolved), ["remove"]);

  const mixed = {
    ...fullyUnresolved,
    deployment_members: [
      { member_role: "primary", mapping_status: "verified", runtime_agent_key: "main" },
      { member_role: "subagent", mapping_status: "unresolved", runtime_agent_key: null },
    ],
  };
  assert.equal(isLegacyUnmanagedDeployment(mixed), false);
  assert.deepEqual(availableAgentDeploymentActions(mixed), []);

  const verifiedChild = {
    ...fullyUnresolved,
    deployment_members: [
      { member_role: "primary", mapping_status: "unresolved", runtime_agent_key: null },
      { member_role: "subagent", mapping_status: "verified", runtime_agent_key: "child" },
    ],
  };
  assert.equal(hasUnsupportedImportedChildMembers(verifiedChild), false);
  assert.deepEqual(availableAgentDeploymentActions(verifiedChild), ["sync"]);
});

test("separates first deployment, sync and migration runtime candidates", () => {
  const runtimes = [
    { runtime_id: "r1", family_code: "openclaw", deployment_mode: "baremetal", lifecycle_status: "active", management_driver: "systemd", management_target: "openclaw", active_primary_agents: 0 },
    { runtime_id: "r2", family_code: "openclaw", deployment_mode: "baremetal", lifecycle_status: "active", management_driver: "systemd", management_target: "openclaw", active_primary_agents: 1 },
    { runtime_id: "r3", family_code: "openclaw", deployment_mode: "container", lifecycle_status: "active", management_driver: "systemd", management_target: "openclaw" },
    { runtime_id: "r4", family_code: "hermes", deployment_mode: "baremetal", lifecycle_status: "active", management_driver: "systemd", management_target: "hermes" },
    { runtime_id: "r5", family_code: "openclaw", deployment_mode: "baremetal", lifecycle_status: "disabled", management_driver: "systemd", management_target: "openclaw" },
    { runtime_id: "r6", family_code: "openclaw", deployment_mode: "baremetal", lifecycle_status: "active", management_driver: "none", management_target: null },
  ];
  assert.deepEqual(eligibleAgentRuntimes(runtimes).map((item) => item.runtime_id), ["r1"]);
  assert.deepEqual(eligibleAgentRuntimes(runtimes, { runtime_id: "r2" }, "sync").map((item) => item.runtime_id), ["r2"]);
  assert.deepEqual(eligibleAgentRuntimes(runtimes, { runtime_id: "r2" }, "migrate").map((item) => item.runtime_id), ["r1"]);
  assert.equal(runtimeSupportsAgentDeployment(runtimes[0]), true);
  assert.equal(runtimeSupportsAgentDeployment(runtimes[2]), false);
});

test("explains occupied candidates and preserves a backend conflict reason", () => {
  const occupied = [
    { runtime_id: "current", family_code: "openclaw", deployment_mode: "baremetal", lifecycle_status: "active", management_driver: "systemd", management_target: "openclaw", active_primary_agents: 1 },
    { runtime_id: "other", family_code: "openclaw", deployment_mode: "baremetal", lifecycle_status: "active", management_driver: "systemd", management_target: "openclaw", active_primary_agents: 1 },
  ];
  assert.equal(
    runtimeCandidateEmptyReason(occupied, { runtime_id: "current" }, "migrate"),
    "所有可迁移的运行时均已被其他主智能体占用，请先解除一个部署关系或新增运行时。",
  );
  assert.equal(
    runtimeCandidateEmptyReason([
      { ...occupied[1], active_primary_agents: 0, deployment_blocked_reason: "目标运行时已有部署任务，请稍后重试。" },
    ], null, "deploy"),
    "目标运行时已有部署任务，请稍后重试。",
  );
  const unmanagedCurrent = {
    runtime_id: "unmanaged",
    family_code: "openclaw",
    deployment_mode: "baremetal",
    lifecycle_status: "active",
    management_driver: "none",
    management_target: null,
    active_primary_agents: 1,
  };
  assert.deepEqual(
    eligibleAgentRuntimes([unmanagedCurrent], { runtime_id: "unmanaged" }, "sync"),
    [],
  );
  assert.equal(
    runtimeCandidateEmptyReason([unmanagedCurrent], { runtime_id: "unmanaged" }, "sync"),
    "当前运行时尚未完成服务管理配置，不能安全执行同步、更换或解除；请先在运行时模块配置管理驱动与服务。",
  );
  assert.equal(
    currentRuntimeDeploymentBlockReason([unmanagedCurrent], { runtime_id: "unmanaged" }),
    "当前运行时尚未完成服务管理配置，不能安全执行同步、更换或解除；请先在运行时模块配置管理驱动与服务。",
  );
  assert.equal(
    currentRuntimeDeploymentBlockReason([{
      ...unmanagedCurrent,
      management_driver: "systemd",
      management_target: "openclaw",
      deployment_eligible: false,
      deployment_blocked_reason: "运行时缺少配置路径或状态目录",
    }], { runtime_id: "unmanaged" }),
    "运行时缺少配置路径或状态目录",
  );
});

test("builds operation-specific deployment payloads without changing the stable runtime key", () => {
  const form = agentDeploymentDefaults(
    { name: "法律助手", agent_id: "a1" },
    { agent_deployment_id: "d1", runtime_id: "r1" },
    [{ member_role: "primary", runtime_agent_key: "legal-main" }],
  );
  assert.equal(form.runtime_agent_key, "legal-main");
  assert.deepEqual(buildAgentDeploymentPayload(form, "sync"), {
    runtime_id: "r1",
    runtime_agent_key: "legal-main",
  });
  assert.deepEqual(buildAgentDeploymentPayload(
    { ...form, runtime_id: "r2", workspace_root: " /data/workspaces ", model: " model-a " },
    "migrate",
    { agent_deployment_id: "d1", runtime_id: "r1" },
  ), {
    runtime_id: "r2",
    runtime_agent_key: "legal-main",
    expected_deployment_id: "d1",
    workspace_root: "/data/workspaces",
    model: "model-a",
  });
  assert.deepEqual(
    buildAgentDeploymentPayload(form, "remove", { agent_deployment_id: "d1" }),
    { expected_deployment_id: "d1" },
  );
  assert.equal(agentDeploymentOperationCode("deploy"), "agent.apply_deployment");
  assert.equal(agentDeploymentOperationCode("sync"), "agent.apply_deployment");
  assert.equal(agentDeploymentOperationCode("migrate"), "agent.migrate_deployment");
  assert.equal(agentDeploymentOperationCode("remove"), "agent.remove_deployment");
});

test("turns structured migration recovery results into actionable feedback", () => {
  assert.equal(agentDeploymentRecoveryStatus({ status: "queued", hosts: [{ result: { recovery_required: true } }] }), "");
  assert.equal(agentDeploymentRecoveryStatus({ status: "running", hosts: [{ result: { target_cleaned: false } }] }), "");
  assert.equal(agentDeploymentRecoveryStatus({
    host_status: "failed",
    result: { recovery_required: true, source_restored: false, target_cleaned: false },
  }), "需要人工恢复");
  assert.equal(agentDeploymentRecoveryStatus({
    status: "succeeded",
    hosts: [{ result: { source_restored: false, target_cleaned: false } }],
  }), "");
  const restored = {
    status: "failed",
    hosts: [{
      error_message: "目标运行时启动失败",
      result: { source_restored: true, target_cleaned: false, recovery_required: false },
    }],
  };
  assert.equal(agentDeploymentRecoveryStatus(restored), "原部署已恢复，目标预置待清理");
  assert.equal(
    agentDeploymentResultMessage("migrate", restored),
    "迁移失败，原部署已自动恢复，目标预置待清理：目标运行时启动失败",
  );

  const manualRecovery = {
    status: "failed",
    hosts: [{
      error_message: "补偿失败",
      result: { source_restored: false, target_cleaned: false, recovery_required: true },
    }],
  };
  assert.equal(agentDeploymentRecoveryStatus(manualRecovery), "需要人工恢复");
  assert.equal(
    agentDeploymentResultMessage("migrate", manualRecovery),
    "迁移未完成，需要人工恢复：源运行时未确认恢复，目标预置未确认清理。补偿失败",
  );
  assert.equal(
    agentDeploymentResultMessage("remove", { status: "failed", hosts: [{ error_message: "远端校验失败" }] }),
    "解除部署失败，原部署关系保持有效：远端校验失败",
  );
  assert.equal(
    agentDeploymentResultMessage("remove", {
      status: "succeeded",
      hosts: [{ result: { relationship_removed: true, remote_cleanup_skipped: true } }],
    }),
    "导入关系已解除；未连接或修改远端运行时",
  );
});
