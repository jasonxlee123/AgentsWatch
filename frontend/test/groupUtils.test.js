import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoGroupDetail, buildDemoGroups, filterAssetsByGroup, filterGroups } from "../src/groupUtils.js";

const servers = [
  { server_id: "s1", name: "Server-1", lifecycle_status: "active", memory_gib: 64 },
  { server_id: "s2", name: "Server-2", lifecycle_status: "active", memory_gib: 128 },
];
const agents = [
  { agent_id: "a1", name: "Agent-1", structure_role: "primary", service_archetype: "department" },
  { agent_id: "a2", name: "Agent-2", structure_role: "primary", service_archetype: "business" },
  { agent_id: "a3", name: "Subagent", structure_role: "subagent", service_archetype: "business" },
];

test("builds separate server and primary-agent groups with overlapping membership", () => {
  const groups = buildDemoGroups(servers, agents);
  const serverGroups = groups.filter((group) => group.resource_type === "server");
  const agentGroups = groups.filter((group) => group.resource_type === "agent");
  assert.equal(serverGroups.length, 3);
  assert.equal(agentGroups[0].members.length, 2);
  assert.ok(serverGroups[0].members.some((member) => serverGroups[1].members.includes(member)));
});

test("filters management groups by type, lifecycle and purpose", () => {
  const groups = buildDemoGroups(servers, agents);
  assert.deepEqual(filterGroups(groups, "容器", "server", "active").map((group) => group.group_code), ["server-container"]);
  assert.deepEqual(filterGroups(groups, "垂域", "agent", "active").map((group) => group.group_code), ["agent-business"]);
});

test("projects type-specific member details without mixing resources", () => {
  const groups = buildDemoGroups(servers, agents);
  const agentGroup = groups.find((group) => group.group_code === "agent-priority");
  const detail = buildDemoGroupDetail(agentGroup, servers, agents, groups);
  assert.equal(detail.resource_type, "agent");
  assert.equal(detail.members.length, 2);
  assert.ok(detail.members.every((member) => member.agent_id));
});

test("builds runtime groups and filters assets using an explicit member set", () => {
  const runtimes = [
    { runtime_id: "r1", name: "Runtime-1", family_code: "openclaw", lifecycle_status: "active" },
    { runtime_id: "r2", name: "Runtime-2", family_code: "other", lifecycle_status: "disabled" },
  ];
  const groups = buildDemoGroups(servers, agents, runtimes);
  const runtimeGroup = groups.find((group) => group.group_code === "runtime-openclaw");
  assert.deepEqual(runtimeGroup.members.map((item) => item.runtime_id), ["r1"]);
  assert.deepEqual(filterAssetsByGroup(runtimes, new Set(["r2"]), "runtime_id").map((item) => item.runtime_id), ["r2"]);
  assert.equal(filterAssetsByGroup(runtimes, null, "runtime_id"), runtimes);
});
