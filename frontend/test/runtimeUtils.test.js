import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoRuntimeDetail, filterRuntimes, flattenRuntimeMap, RUNTIME_MANAGEMENT_DRIVER_LABELS } from "../src/runtimeUtils.js";

const servers = [{ server_id: "demo-server-1", name: "示例服务器 1", internal_ip: "192.0.2.1" }];
const runtimeMap = { "demo-server-1": [
  { runtime_id: "r1", name: "OpenClaw Runtime", family_code: "openclaw", deployment_mode: "baremetal" },
  { runtime_id: "r2", name: "Hermes Runtime", family_code: "hermes", deployment_mode: "container" },
] };

test("flattens runtime relationships with server identity", () => {
  const runtimes = flattenRuntimeMap(runtimeMap, servers);
  assert.equal(runtimes[0].server_name, "示例服务器 1");
  assert.equal(runtimes[0].server_internal_ip, "192.0.2.1");
});

test("filters runtimes by localized name, family and deployment mode", () => {
  const runtimes = flattenRuntimeMap(runtimeMap, servers);
  assert.deepEqual(filterRuntimes(runtimes, "运行时", "hermes", "container").map((item) => item.runtime_id), ["r2"]);
  assert.deepEqual(filterRuntimes(runtimes, "示例服务器 1", "all", "baremetal").map((item) => item.runtime_id), ["r1"]);
});

test("creates snapshot and active deployment projections for demo review", () => {
  const runtime = flattenRuntimeMap(runtimeMap, servers)[0];
  const detail = buildDemoRuntimeDetail(runtime, { primary: { agent_id: "a1", name: "示例合规智能体", service_archetype: "department" }, subagents: [{}, {}] });
  assert.equal(detail.latest_snapshot.observed_agent_count, 3);
  assert.equal(detail.active_deployments[0].agent_name, "示例合规智能体");
});

test("labels systemd user services distinctly from system services", () => {
  assert.equal(RUNTIME_MANAGEMENT_DRIVER_LABELS.systemd, "systemd 系统服务");
  assert.equal(RUNTIME_MANAGEMENT_DRIVER_LABELS.systemd_user, "systemd 用户服务");
});
